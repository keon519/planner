"""Unit tests for the pure logic. No network, no server, no API key needed."""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def conn():
    from app import config, db
    tmp = Path(tempfile.mkdtemp())
    db.DB_PATH = config.DB_PATH = tmp / "t.db"
    db.UPLOADS = config.UPLOADS = tmp / "up"
    db.UPLOADS.mkdir(parents=True, exist_ok=True)
    db.init()
    c = db.connect()
    yield c
    c.close()


def _mk(conn, title, hours_out, steps, kind="deliverable", cid=None):
    from app import db
    due = (datetime.now(timezone.utc) + timedelta(hours=hours_out)).isoformat()
    cur = conn.execute(
        "INSERT INTO assignments (class_id,title,kind,due_at,created_at) "
        "VALUES (?,?,?,?,?)", (cid, title, kind, due, db.utcnow()))
    for i, (t, m) in enumerate(steps, 1):
        conn.execute("INSERT INTO subtasks (assignment_id,seq,title,est_minutes) "
                     "VALUES (?,?,?,?)", (cur.lastrowid, i, t, m))
    conn.commit()
    return cur.lastrowid


# ------------------------------------------------------------------ pricing

def test_pricing_matches_published_rates():
    from app.ai import price_call

    class U:
        input_tokens, output_tokens = 10_000, 2_000
        cache_creation_input_tokens = cache_read_input_tokens = 0

    got = price_call("claude-sonnet-4-6", U())["cost_usd"]
    assert got == pytest.approx((10_000 * 3 + 2_000 * 15) / 1e6)


def test_cache_tokens_priced_at_their_own_rates():
    from app.ai import price_call

    class U:
        input_tokens, output_tokens = 0, 0
        cache_creation_input_tokens, cache_read_input_tokens = 1_000, 10_000

    got = price_call("claude-sonnet-4-6", U())["cost_usd"]
    assert got == pytest.approx((1_000 * 3 * 1.25 + 10_000 * 3 * 0.10) / 1e6)


# ------------------------------------------------------------------ ranking

def test_pressure_beats_raw_deadline(conn):
    """An essay due Friday with 12h left outranks a quiz tomorrow with 20 min."""
    from app.services.ranking import pick_now
    _mk(conn, "Small quiz", 20, [("Skim guide", 20)])
    _mk(conn, "Big essay", 70, [("Read", 10), ("Draft", 600), ("Revise", 400)])
    assert pick_now(conn)["step"]["assignment"] == "Big essay"


def test_only_first_open_step_is_eligible(conn):
    from app.services.ranking import pick_now
    _mk(conn, "Essay", 48, [("One", 30), ("Two", 30), ("Three", 30)])
    assert pick_now(conn)["step"]["title"] == "One"


def test_assignment_without_steps_is_surfaced_not_lost(conn):
    from app import db
    from app.services.ranking import pick_now
    conn.execute("INSERT INTO assignments (title,due_at,created_at) VALUES (?,?,?)",
                 ("Orphan", (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                  db.utcnow()))
    conn.commit()
    assert [x["title"] for x in pick_now(conn)["needs_breakdown"]] == ["Orphan"]


# --------------------------------------------------------------- scheduling

def test_blocks_never_overlap_events(conn):
    from app import db
    from app.services.scheduling import plan_horizon
    local = datetime.now().astimezone()
    start = local.replace(hour=12, minute=0, second=0, microsecond=0)
    conn.execute("INSERT INTO events (title,start_at,end_at) VALUES (?,?,?)",
                 ("Lecture", start.astimezone(timezone.utc).isoformat(),
                  (start + timedelta(hours=1)).astimezone(timezone.utc).isoformat()))
    _mk(conn, "Essay", 96, [("A", 60), ("B", 60), ("C", 60)])
    conn.commit()
    plan = plan_horizon(conn, days=3)
    for d in plan["days"]:
        for b in d["blocks"]:
            bs, be = datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])
            assert be <= start.astimezone(timezone.utc) or \
                   bs >= (start + timedelta(hours=1)).astimezone(timezone.utc)


def test_daily_cap_is_respected(conn):
    from app.services.scheduling import DEFAULTS, plan_horizon
    _mk(conn, "Huge", 24 * 14, [(f"Step {i}", 90) for i in range(20)])
    for d in plan_horizon(conn, days=10)["days"]:
        assert d["planned_minutes"] <= DEFAULTS["max_daily_min"]


def test_nothing_scheduled_after_its_own_deadline(conn):
    from app.services.scheduling import plan_horizon
    _mk(conn, "Soon", 30, [("A", 60), ("B", 60)])
    for d in plan_horizon(conn, days=5)["days"]:
        for b in d["blocks"]:
            assert datetime.fromisoformat(b["end"]) <= datetime.fromisoformat(b["due_at"])


def test_overdue_work_is_still_scheduled(conn):
    """It's late, but it still has to happen — and it goes first."""
    from app.services.scheduling import plan_horizon
    _mk(conn, "Forgotten", -48, [("Finish it", 40)])
    _mk(conn, "Upcoming", 72, [("Draft", 45)])
    plan = plan_horizon(conn, days=4)
    first = next(b for d in plan["days"] for b in d["blocks"])
    assert first["assignment"] == "Forgotten"
    assert not plan["unplaceable"]


def test_exam_sessions_are_spaced_not_crammed(conn):
    from app.services.scheduling import plan_horizon
    _mk(conn, "Midterm", 24 * 7, [(f"Session {i}", 50) for i in range(4)], kind="exam")
    for d in plan_horizon(conn, days=8)["days"]:
        assert len([b for b in d["blocks"] if b["kind"] == "exam"]) <= 1


def test_gated_step_waits_for_its_material(conn):
    from app import db
    from app.services.scheduling import plan_horizon
    gate = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    aid = _mk(conn, "Response", 24 * 6, [("Prep now", 30)])
    conn.execute("INSERT INTO subtasks (assignment_id,seq,title,est_minutes,not_before) "
                 "VALUES (?,2,?,?,?)", (aid, "Needs lecture", 60, gate))
    conn.commit()
    for d in plan_horizon(conn, days=6)["days"]:
        for b in d["blocks"]:
            if b["title"] == "Needs lecture":
                assert datetime.fromisoformat(b["start"]) >= datetime.fromisoformat(gate)


def test_free_windows_invert_busy_intervals():
    from app.services.scheduling import free_windows
    base = datetime(2026, 8, 17, 8, tzinfo=timezone.utc)
    busy = [(base + timedelta(hours=2), base + timedelta(hours=3))]
    got = free_windows(busy, base, base + timedelta(hours=6), buffer_min=10, min_chunk=20)
    assert len(got) == 2
    assert got[0][1] == base + timedelta(hours=1, minutes=50)   # buffer applied
    assert got[1][0] == base + timedelta(hours=3, minutes=10)




# -------------------------------------------------------------- calibration

def test_calibration_stays_quiet_until_it_has_evidence(conn):
    from app.services.context import calibration
    assert calibration(conn)["ratio"] is None


def test_calibration_detects_a_consistent_overrun(conn):
    from app import db
    from app.services.context import calibration
    aid = _mk(conn, "Work", 48, [])
    now = datetime.now(timezone.utc)
    for i in range(8):
        start = now - timedelta(days=3, minutes=i)
        conn.execute(
            "INSERT INTO subtasks (assignment_id,seq,title,est_minutes,status,"
            "completed_at,planned_start) VALUES (?,?,?,30,'done',?,?)",
            (aid, i, f"s{i}", (start + timedelta(minutes=45)).isoformat(),
             start.isoformat()))
    conn.commit()
    assert calibration(conn)["ratio"] == pytest.approx(1.5, abs=0.05)
