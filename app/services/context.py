"""Assemble what Claude should know about the rest of your workload.

Decomposing an assignment in isolation produces steps that re-plan reading you
already finished for a different essay in the same course, and that ignore the
three other deadlines in the same week. This gathers the surrounding picture.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

MIN_CALIBRATION_SAMPLES = 5


def _aware(iso: str) -> datetime:
    d = datetime.fromisoformat(iso)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _when(iso: str | None, now: datetime) -> str:
    if not iso:
        return "no due date"
    days = (_aware(iso) - now).total_seconds() / 86400
    if days < -1:
        return f"overdue by {abs(int(days))}d"
    if days < 1:
        return "due today"
    return f"due in {int(days)}d"


def calibration(conn: sqlite3.Connection) -> dict:
    """How the person's real pace compares to the estimates.

    Measured as time from a step's planned start to when it was marked done.
    That's a proxy, not a stopwatch — marking done late inflates it — so
    samples beyond 3x the estimate are discarded as "forgot to tick it off",
    and the result is only reported once there's enough data to mean anything.
    """
    rows = conn.execute("""
        SELECT est_minutes, planned_start, completed_at FROM subtasks
         WHERE status = 'done' AND planned_start IS NOT NULL
           AND completed_at IS NOT NULL AND est_minutes > 0""").fetchall()
    ratios = []
    for r in rows:
        actual = (_aware(r["completed_at"]) - _aware(r["planned_start"])).total_seconds() / 60
        if actual <= 0:
            continue
        ratio = actual / r["est_minutes"]
        if 0.2 <= ratio <= 3.0:
            ratios.append(ratio)
    if len(ratios) < MIN_CALIBRATION_SAMPLES:
        return {"samples": len(ratios), "ratio": None}
    ratios.sort()
    median = ratios[len(ratios) // 2]
    return {"samples": len(ratios), "ratio": round(median, 2)}


def build(conn: sqlite3.Connection, aid: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    a = conn.execute("SELECT * FROM assignments WHERE id = ?", (aid,)).fetchone()
    if not a:
        return {}

    klass = None
    if a["class_id"]:
        klass = conn.execute(
            "SELECT code, name, context, topic_schedule FROM classes WHERE id = ?",
            (a["class_id"],)).fetchone()

    # Other work in the same course — the strongest signal for overlap.
    siblings = [dict(r) for r in conn.execute("""
        SELECT id, title, kind, due_at, status, substr(description,1,220) AS gist
          FROM assignments
         WHERE class_id IS ? AND id != ?
         ORDER BY COALESCE(due_at,'9999') DESC LIMIT 8""",
        (a["class_id"], aid))] if a["class_id"] else []

    # Work already finished for this course, so it isn't planned twice.
    finished = [dict(r) for r in conn.execute("""
        SELECT s.title, a2.title AS assignment, s.completed_at
          FROM subtasks s JOIN assignments a2 ON a2.id = s.assignment_id
         WHERE a2.class_id IS ? AND s.status = 'done' AND s.completed_at IS NOT NULL
         ORDER BY s.completed_at DESC LIMIT 15""",
        (a["class_id"],))] if a["class_id"] else []

    # Everything else competing for the same hours. Assignments whose steps are
    # all done aren't competing for anything; ones not yet broken down are.
    competing = [dict(r) for r in conn.execute("""
        SELECT a2.title, a2.kind, a2.due_at, c.code AS class_code,
               (SELECT COALESCE(SUM(est_minutes),0) FROM subtasks
                 WHERE assignment_id = a2.id AND status = 'todo') AS left_min,
               (SELECT COUNT(*) FROM subtasks WHERE assignment_id = a2.id) AS n_steps
          FROM assignments a2 LEFT JOIN classes c ON c.id = a2.class_id
         WHERE a2.status = 'todo' AND a2.id != ? AND a2.due_at IS NOT NULL
           AND a2.due_at <= ?
           AND (n_steps = 0 OR left_min > 0)
         ORDER BY a2.due_at LIMIT 8""",
        (aid, (now + timedelta(days=21)).isoformat()))]

    topics = []
    if klass and klass["topic_schedule"]:
        try:
            topics = json.loads(klass["topic_schedule"])
        except (ValueError, TypeError):
            topics = []

    return {
        "assignment": dict(a),
        "class": dict(klass) if klass else None,
        "topic_schedule": topics,
        "siblings": siblings,
        "finished_steps": finished,
        "competing": competing,
        "calibration": calibration(conn),
        "now": now.isoformat(),
    }


def to_prompt(ctx: dict) -> str:
    """Render the context as the block appended to the decompose request."""
    if not ctx:
        return ""
    now = _aware(ctx["now"])
    out = []

    k = ctx.get("class")
    if k:
        out.append(f"COURSE: {k['code']} — {k['name']}")
        if k.get("context"):
            out.append(k["context"])

    if ctx.get("topic_schedule"):
        out.append("\nCOURSE TOPIC CALENDAR (material is taught on these dates):")
        out += [f"  {e['date']}: {e['topics']}" for e in ctx["topic_schedule"][:40]]

    sib = ctx.get("siblings") or []
    if sib:
        out.append("\nOTHER WORK IN THIS COURSE:")
        for s in sib:
            state = "finished" if s["status"] != "todo" else _when(s["due_at"], now)
            line = f"  - {s['title']} ({s['kind']}, {state})"
            if s.get("gist"):
                line += f"\n      {s['gist']}"
            out.append(line)

    fin = ctx.get("finished_steps") or []
    if fin:
        out.append("\nWORK ALREADY COMPLETED FOR THIS COURSE:")
        out += [f"  - {f['title']}  [{f['assignment']}]" for f in fin]

    comp = ctx.get("competing") or []
    if comp:
        total = sum(c["left_min"] for c in comp)
        out.append(f"\nOTHER DEADLINES COMPETING FOR THE SAME HOURS "
                   f"({total} min of work outstanding elsewhere):")
        for c in comp:
            out.append(f"  - {c['title']} ({c.get('class_code') or 'no class'}, "
                       f"{_when(c['due_at'], now)}, {c['left_min']} min left)")

    cal = ctx.get("calibration") or {}
    if cal.get("ratio"):
        pct = int(abs(cal["ratio"] - 1) * 100)
        if cal["ratio"] > 1.1:
            out.append(f"\nPACE: this student's steps typically take about {pct}% "
                       f"LONGER than estimated (median over {cal['samples']} steps). "
                       f"Scale your estimates up accordingly.")
        elif cal["ratio"] < 0.9:
            out.append(f"\nPACE: this student typically finishes about {pct}% FASTER "
                       f"than estimated (median over {cal['samples']} steps). "
                       f"Scale your estimates down accordingly.")

    return "\n".join(out)
