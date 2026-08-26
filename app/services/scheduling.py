"""Place study blocks into the gaps between calendar events.

The rule is the same one `pick_now` uses: pressure, meaning work remaining
divided by time remaining. The difference is that here pressure is recomputed
after every placement, so a heavy assignment naturally yields the floor once
enough of it has been scheduled — which is what produces interleaving rather
than one assignment monopolising the day.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

DEFAULTS = {
    "day_start_hour": 8,       # don't schedule before this
    "day_end_hour": 23,        # or after this
    "min_chunk": 20,           # a 10-minute sliver isn't a study block
    "buffer_min": 10,          # breathing room either side of an event
    "max_block": 90,           # break up anything longer
    "max_daily_min": 240,      # cap on study per day, or it all lands today
    "exam_per_day": 1,         # spacing beats cramming for exams
    "horizon_days": 14,        # how far ahead to plan
}

UNDATED_PRESSURE = 0.05


def _aware(iso: str) -> datetime:
    d = datetime.fromisoformat(iso)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------ free windows

def merge(intervals: list[tuple]) -> list[tuple]:
    if not intervals:
        return []
    out = [list(intervals[0])]
    for s, e in sorted(intervals)[1:]:
        if s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [tuple(x) for x in out]


def free_windows(busy: list[tuple], start: datetime, end: datetime,
                 buffer_min: int, min_chunk: int) -> list[tuple]:
    """Invert busy intervals into usable gaps, with a buffer around each."""
    padded = [(s - timedelta(minutes=buffer_min), e + timedelta(minutes=buffer_min))
              for s, e in busy]
    gaps, cursor = [], start
    for s, e in merge(padded):
        if s > cursor:
            gaps.append((cursor, min(s, end)))
        cursor = max(cursor, e)
        if cursor >= end:
            break
    if cursor < end:
        gaps.append((cursor, end))
    return [(s, e) for s, e in gaps
            if s < e and (e - s).total_seconds() / 60 >= min_chunk]


# ------------------------------------------------------------- the planner

def plan_day(conn: sqlite3.Connection, day: datetime | None = None,
             settings: dict | None = None, now: datetime | None = None) -> dict:
    cfg = {**DEFAULTS, **(settings or {})}
    now = now or datetime.now(timezone.utc)
    day = day or now
    local_day = day.astimezone()

    start = local_day.replace(hour=int(cfg["day_start_hour"]), minute=0,
                              second=0, microsecond=0)
    end = local_day.replace(hour=int(cfg["day_end_hour"]), minute=0,
                            second=0, microsecond=0)
    # Never plan into the past.
    plan_from = max(start, now.astimezone()) if local_day.date() == now.astimezone().date() else start

    # --- events for the day, recurrence already expanded upstream
    from .ranking import expand_events
    events = expand_events(conn, start.isoformat(), end.isoformat())
    busy = []
    for e in events:
        s = _aware(e["occurrence_start"])
        fin = _aware(e["occurrence_end"]) if e.get("occurrence_end") else \
              (_aware(e["end_at"]) if e["end_at"] else s + timedelta(hours=1))
        busy.append((s, fin))

    windows = free_windows(busy, plan_from, end,
                           int(cfg["buffer_min"]), int(cfg["min_chunk"]))

    # --- open work, grouped by assignment and kept in step order
    rows = conn.execute("""
        SELECT s.id, s.title, s.est_minutes, s.seq,
               a.id AS aid, a.title AS assignment, a.due_at,
               c.code AS class_code, c.color
          FROM subtasks s
          JOIN assignments a ON a.id = s.assignment_id
     LEFT JOIN classes c     ON c.id = a.class_id
         WHERE a.status = 'todo' AND a.confirmed = 1 AND s.status = 'todo'
           AND (s.snoozed_until IS NULL OR s.snoozed_until < ?)
         ORDER BY a.id, s.seq""", (now.isoformat(),)).fetchall()

    queues: dict[int, list] = {}
    meta: dict[int, dict] = {}
    for r in rows:
        queues.setdefault(r["aid"], []).append(
            {"id": r["id"], "title": r["title"], "left": r["est_minutes"],
             "total": r["est_minutes"]})
        meta.setdefault(r["aid"], {
            "assignment": r["assignment"], "due_at": r["due_at"],
            "class_code": r["class_code"], "color": r["color"] or "#6B7FD7"})

    def remaining(aid):
        return sum(s["left"] for s in queues[aid])

    def pressure(aid, at):
        due = meta[aid]["due_at"]
        if not due:
            return UNDATED_PRESSURE
        mins = (_aware(due) - at).total_seconds() / 60
        if mins <= 0:
            return 1e6
        return remaining(aid) / mins

    blocks, total_work = [], sum(remaining(a) for a in queues)

    for w_start, w_end in windows:
        cursor = w_start
        while (w_end - cursor).total_seconds() / 60 >= cfg["min_chunk"]:
            live = [a for a in queues if queues[a]]
            if not live:
                break
            aid = max(live, key=lambda a: pressure(a, cursor))
            step = queues[aid][0]
            room = (w_end - cursor).total_seconds() / 60
            take = int(min(step["left"], room, cfg["max_block"]))
            # min_chunk governs SPLITTING, not whole steps: a complete 10-minute
            # step is legitimately 10 minutes, but never leave a 6-minute stub.
            if step["left"] > room and take < cfg["min_chunk"]:
                break
            fin = cursor + timedelta(minutes=take)
            blocks.append({
                "start": cursor.isoformat(),
                "end": fin.isoformat(),
                "minutes": take,
                "subtask_id": step["id"],
                "title": step["title"],
                "partial": take < step["left"],
                "assignment": meta[aid]["assignment"],
                "class_code": meta[aid]["class_code"],
                "color": meta[aid]["color"],
                "due_at": meta[aid]["due_at"],
            })
            step["left"] -= take
            if step["left"] <= 0:
                queues[aid].pop(0)
            cursor = fin
        if not any(queues[a] for a in queues):
            break

    placed = sum(b["minutes"] for b in blocks)
    free_total = sum((e - s).total_seconds() / 60 for s, e in windows)

    return {
        "day_start": start.isoformat(),
        "day_end": end.isoformat(),
        "now": now.isoformat(),
        "events": [{
            "title": e["title"],
            "start": e["occurrence_start"],
            "end": e.get("occurrence_end") or e["end_at"],
            "location": e["location"],
            "class_code": e["class_code"],
            "color": e["color"] or "#565C6E",
        } for e in events],
        "blocks": blocks,
        "planned_minutes": placed,
        "free_minutes": int(free_total),
        "unplaced_minutes": int(total_work - placed),
        "total_work_minutes": int(total_work),
    }


# ------------------------------------------------------ horizon planner

def _day_bounds(day_date, cfg, tz):
    from datetime import time as _time
    start = datetime.combine(day_date, _time(int(cfg["day_start_hour"])), tzinfo=tz)
    end = datetime.combine(day_date, _time(int(cfg["day_end_hour"])), tzinfo=tz)
    return start, end


def plan_horizon(conn: sqlite3.Connection, settings: dict | None = None,
                 now: datetime | None = None, days: int | None = None) -> dict:
    """Place every open step across the days between now and its deadline.

    Priorities, in order:
      1. never schedule a step so late it finishes after its own due date
      2. earlier days before later days (front-load)
      3. within a day, whichever assignment has the highest pressure
      4. subject to a daily cap, so front-loading doesn't mean a 12-hour Monday

    Exams are the exception to front-loading: sessions are limited to one per
    day so revision spaces out instead of clustering.
    """
    cfg = {**DEFAULTS, **(settings or {})}
    now = now or datetime.now(timezone.utc)
    tz = now.astimezone().tzinfo
    local_now = now.astimezone()
    horizon = int(days or cfg["horizon_days"])

    rows = conn.execute("""
        SELECT s.id, s.title, s.est_minutes, s.seq, s.not_before,
               a.id AS aid, a.title AS assignment, a.due_at, a.kind,
               c.code AS class_code, c.color
          FROM subtasks s
          JOIN assignments a ON a.id = s.assignment_id
     LEFT JOIN classes c     ON c.id = a.class_id
         WHERE a.status = 'todo' AND a.confirmed = 1 AND s.status = 'todo'
           AND (s.snoozed_until IS NULL OR s.snoozed_until < ?)
         ORDER BY a.id, s.seq""", (now.isoformat(),)).fetchall()

    queues: dict[int, list] = {}
    meta: dict[int, dict] = {}
    for r in rows:
        queues.setdefault(r["aid"], []).append(
            {"id": r["id"], "title": r["title"], "left": r["est_minutes"],
             "est": r["est_minutes"],
             "not_before": _aware(r["not_before"]) if r["not_before"] else None})
        meta.setdefault(r["aid"], {
            "assignment": r["assignment"], "due_at": r["due_at"], "kind": r["kind"],
            "class_code": r["class_code"], "color": r["color"] or "#6B7FD7"})

    def remaining(aid):
        return sum(s["left"] for s in queues[aid])

    def due(aid):
        d = meta[aid]["due_at"]
        return _aware(d) if d else None

    def pressure(aid, at):
        d = due(aid)
        if not d:
            return UNDATED_PRESSURE
        mins = (d - at).total_seconds() / 60
        return 1e6 if mins <= 0 else remaining(aid) / mins

    from .ranking import expand_events

    # Slots already spent on completed steps are occupied. Without this, each
    # completion frees its slot and the next step slides into it — so every
    # step ends up recording the same start time.
    spent = []
    for r in conn.execute("""
            SELECT planned_start, planned_end FROM subtasks
             WHERE status = 'done' AND planned_start IS NOT NULL
               AND planned_end IS NOT NULL AND planned_end >= ?""",
            ((now - timedelta(days=1)).isoformat(),)):
        spent.append((_aware(r["planned_start"]), _aware(r["planned_end"])))

    total_work = sum(remaining(a) for a in queues)
    day_plans = []

    for offset in range(horizon):
        d_date = (local_now + timedelta(days=offset)).date()
        d_start, d_end = _day_bounds(d_date, cfg, tz)
        if offset == 0:
            d_start = max(d_start, local_now)
        if d_start >= d_end:
            day_plans.append({"date": d_date.isoformat(), "blocks": [],
                              "planned_minutes": 0, "free_minutes": 0})
            continue

        events = expand_events(conn, d_start.isoformat(), d_end.isoformat())
        busy = []
        for e in events:
            es = _aware(e["occurrence_start"])
            ee = (_aware(e["occurrence_end"]) if e.get("occurrence_end")
                  else (_aware(e["end_at"]) if e["end_at"] else es + timedelta(hours=1)))
            busy.append((es, ee))

        # Completed slots block the planner but get no buffer around them —
        # you can start the next thing the moment you finish.
        busy_padded = [(b - timedelta(minutes=int(cfg["buffer_min"])),
                        e + timedelta(minutes=int(cfg["buffer_min"])))
                       for b, e in busy]
        windows = free_windows(busy_padded + spent, d_start, d_end,
                               0, int(cfg["min_chunk"]))
        free_min = sum((e - s).total_seconds() / 60 for s, e in windows)

        blocks, placed_today = [], 0
        exams_today: dict[int, int] = {}

        for w_start, w_end in windows:
            cursor = w_start
            while True:
                room = (w_end - cursor).total_seconds() / 60
                budget = cfg["max_daily_min"] - placed_today
                if room < cfg["min_chunk"] or budget < cfg["min_chunk"]:
                    break

                # Candidates: has work, deadline still ahead, and for exams
                # hasn't already had its session today.
                skip: set[int] = set()
                placed_one = False
                while True:
                    live = [a for a in queues
                            if queues[a] and a not in skip
                            # Overdue work is still work: it can't be finished
                            # "before the deadline", but it must still be
                            # scheduled — and first.
                            and (due(a) is None or due(a) > cursor
                                 or due(a) < local_now)
                            # Steps are ordered, so a gated head step gates
                            # the whole assignment until the material lands.
                            and (queues[a][0]["not_before"] is None
                                 or queues[a][0]["not_before"] <= cursor)
                            and not (meta[a]["kind"] == "exam"
                                     and exams_today.get(a, 0) >= cfg["exam_per_day"])]
                    if not live:
                        break
                    aid = max(live, key=lambda a: pressure(a, cursor))
                    step = queues[aid][0]

                    cap = min(step["left"], room, budget, cfg["max_block"])
                    d = due(aid)
                    if d and d > local_now:          # not applicable once overdue
                        cap = min(cap, (d - cursor).total_seconds() / 60)
                    take = int(cap)

                    # When splitting a step, both halves must be usable —
                    # otherwise you get a 5-minute orphan block tomorrow.
                    if take < step["left"] and (step["left"] - take) < cfg["min_chunk"]:
                        take = int(step["left"] - cfg["min_chunk"])

                    # If no usable bite fits here, try the next assignment
                    # rather than forcing a stub.
                    if take < min(cfg["min_chunk"], step["left"]):
                        skip.add(aid)
                        continue

                    fin = cursor + timedelta(minutes=take)
                    blocks.append({
                        "start": cursor.isoformat(), "end": fin.isoformat(),
                        "minutes": take, "subtask_id": step["id"],
                        "title": step["title"], "partial": take < step["left"],
                        "assignment": meta[aid]["assignment"],
                        "class_code": meta[aid]["class_code"],
                        "color": meta[aid]["color"], "due_at": meta[aid]["due_at"],
                        "kind": meta[aid]["kind"],
                    })
                    step["left"] -= take
                    if step["left"] <= 0:
                        queues[aid].pop(0)
                    if meta[aid]["kind"] == "exam":
                        exams_today[aid] = exams_today.get(aid, 0) + 1
                    placed_today += take
                    cursor = fin
                    placed_one = True
                    break

                if not placed_one:
                    break

        day_plans.append({
            "date": d_date.isoformat(),
            "blocks": blocks,
            "planned_minutes": sum(b["minutes"] for b in blocks),
            "free_minutes": int(free_min),
        })

    # Anything still queued either has no room before its deadline, or is
    # gated on material that isn't taught until after the horizon/deadline.
    unplaceable = []
    end_of_horizon = local_now + timedelta(days=horizon)
    for aid, q in queues.items():
        left = sum(s["left"] for s in q)
        if left > 0:
            head_gate = q[0]["not_before"]
            gated = bool(head_gate and head_gate > local_now)
            d = due(aid)
            reason = "gated_past_due" if (gated and d and head_gate >= d)                 else "waiting_on_material" if gated else "no_room"
            unplaceable.append({
                "assignment": meta[aid]["assignment"],
                "class_code": meta[aid]["class_code"],
                "color": meta[aid]["color"],
                "due_at": meta[aid]["due_at"],
                "minutes": left,
                "steps": [s["title"] for s in q],
                "reason": reason,
                "not_before": head_gate.isoformat() if head_gate else None,
            })

    planned = sum(d["planned_minutes"] for d in day_plans)
    return {
        "now": now.isoformat(),
        "days": day_plans,
        "unplaceable": unplaceable,
        "total_work_minutes": int(total_work),
        "planned_minutes": planned,
        "unplaced_minutes": int(total_work - planned),
        "horizon_days": horizon,
    }


# ------------------------------------------------------- rolling window

def window_view(conn: sqlite3.Connection, back_min: int = 240,
                fwd_min: int = 300, settings: dict | None = None) -> dict:
    """A rolling band around the present moment.

    The past half can't contain planned blocks — planning only looks forward.
    It shows what actually happened instead: events, and steps you completed,
    drawn back from their completion time by their estimate.
    """
    now = datetime.now(timezone.utc)
    w_start = now - timedelta(minutes=back_min)
    w_end = now + timedelta(minutes=fwd_min)

    horizon = plan_horizon(conn, settings=settings, now=now)
    today = horizon["days"][0] if horizon["days"] else {"blocks": [], "planned_minutes": 0, "free_minutes": 0}
    cfg = {**DEFAULTS, **(settings or {})}
    tz = now.astimezone().tzinfo
    d_start, d_end = _day_bounds(now.astimezone().date(), cfg, tz)

    from .ranking import expand_events
    # Pad the fetch so scrolling never reveals an empty edge before the
    # next refresh lands.
    events = expand_events(conn,
                           (w_start - timedelta(hours=2)).isoformat(),
                           (w_end + timedelta(hours=2)).isoformat())

    # Completed steps keep the slot they were planned into, so finishing one
    # turns its block green in place rather than stacking a new block at the
    # moment you tapped Done. Steps completed off-plan fall back to an inferred
    # span, chained so consecutive completions can't overlap.
    lo = (w_start - timedelta(hours=3)).isoformat()
    done, prev_start = [], None
    rows_done = conn.execute("""
        SELECT s.title, s.est_minutes, s.completed_at,
               s.planned_start, s.planned_end,
               a.title AS assignment, c.code AS class_code, c.color
          FROM subtasks s
          JOIN assignments a ON a.id = s.assignment_id
     LEFT JOIN classes c     ON c.id = a.class_id
         WHERE s.status = 'done' AND s.completed_at IS NOT NULL
           AND COALESCE(s.planned_end, s.completed_at) >= ?
           AND s.completed_at <= ?
         ORDER BY s.completed_at DESC""", (lo, now.isoformat())).fetchall()

    for r in rows_done:
        if r["planned_start"] and r["planned_end"]:
            st, fin = _aware(r["planned_start"]), _aware(r["planned_end"])
            on_plan = True
        else:
            fin = _aware(r["completed_at"])
            st = fin - timedelta(minutes=r["est_minutes"])
            # Walking newest-first: nothing may run past the previous start.
            if prev_start and fin > prev_start:
                fin = prev_start
                st = min(st, fin - timedelta(minutes=5))
            on_plan = False
        prev_start = st
        done.append({
            "title": r["title"],
            "assignment": r["assignment"],
            "class_code": r["class_code"],
            "color": r["color"] or "#6B7FD7",
            "minutes": r["est_minutes"],
            "start": st.isoformat(),
            "end": fin.isoformat(),
            "on_plan": on_plan,
            "completed_at": r["completed_at"],
        })
    done.reverse()

    return {
        "now": now.isoformat(),
        "window_start": w_start.isoformat(),
        "window_end": w_end.isoformat(),
        "back_min": back_min,
        "fwd_min": fwd_min,
        "day_start": d_start.isoformat(),
        "day_end": d_end.isoformat(),
        "events": [{
            "title": e["title"],
            "start": e["occurrence_start"],
            "end": e.get("occurrence_end") or e["end_at"],
            "location": e["location"],
            "class_code": e["class_code"],
            "color": e["color"] or "#565C6E",
        } for e in events],
        "blocks": today["blocks"],
        "completed": done,
        "planned_minutes": today["planned_minutes"],
        "free_minutes": today["free_minutes"],
        "unplaced_minutes": horizon["unplaced_minutes"],
        "total_work_minutes": horizon["total_work_minutes"],
        "unplaceable": horizon["unplaceable"],
        "upcoming": [{"date": d["date"], "planned_minutes": d["planned_minutes"]}
                     for d in horizon["days"][:8]],
    }


# --------------------------------------------------------- calendar view

def calendar_view(conn: sqlite3.Connection, start_date: str, days: int = 42,
                  settings: dict | None = None,
                  now: datetime | None = None) -> dict:
    """Per-day view for the calendar: what's due, what's on, what's planned.

    Study blocks only exist inside the planning horizon, so days past it carry
    deadlines and events but no blocks. That's reported rather than hidden —
    an empty day beyond the horizon means "not planned yet", not "nothing to do".
    """
    from datetime import date as _date

    now = now or datetime.now(timezone.utc)
    tz = now.astimezone().tzinfo
    first = _date.fromisoformat(start_date)
    last = first + timedelta(days=days - 1)

    win_start = datetime.combine(first, datetime.min.time(), tzinfo=tz)
    win_end = datetime.combine(last, datetime.max.time(), tzinfo=tz)

    horizon = plan_horizon(conn, settings=settings, now=now)
    blocks_by_day: dict[str, list] = {}
    for d in horizon["days"]:
        if d["blocks"]:
            blocks_by_day[d["date"]] = d["blocks"]
    horizon_last = horizon["days"][-1]["date"] if horizon["days"] else None

    from .ranking import expand_events
    events_by_day: dict[str, list] = {}
    for e in expand_events(conn, win_start.isoformat(), win_end.isoformat()):
        key = _aware(e["occurrence_start"]).astimezone(tz).date().isoformat()
        events_by_day.setdefault(key, []).append({
            "title": e["title"],
            "start": e["occurrence_start"],
            "end": e.get("occurrence_end") or e["end_at"],
            "location": e["location"],
            "class_code": e["class_code"],
            "color": e["color"] or "#565C6E",
        })

    due_by_day: dict[str, list] = {}
    for r in conn.execute("""
            SELECT a.id, a.title, a.kind, a.due_at, a.status,
                   c.code AS class_code, c.color,
                   (SELECT COUNT(*) FROM subtasks WHERE assignment_id=a.id) AS total_n,
                   (SELECT COUNT(*) FROM subtasks
                     WHERE assignment_id=a.id AND status!='todo')            AS done_n,
                   (SELECT COALESCE(SUM(est_minutes),0) FROM subtasks
                     WHERE assignment_id=a.id AND status='todo')             AS left_min
              FROM assignments a LEFT JOIN classes c ON c.id = a.class_id
             WHERE a.due_at IS NOT NULL AND a.due_at >= ? AND a.due_at <= ?
             ORDER BY a.due_at""",
            (win_start.astimezone(timezone.utc).isoformat(),
             win_end.astimezone(timezone.utc).isoformat())):
        key = _aware(r["due_at"]).astimezone(tz).date().isoformat()
        due_by_day.setdefault(key, []).append({**dict(r),
                                               "color": r["color"] or "#6B7FD7"})

    out = []
    for i in range(days):
        d = (first + timedelta(days=i)).isoformat()
        blocks = blocks_by_day.get(d, [])
        out.append({
            "date": d,
            "due": due_by_day.get(d, []),
            "events": events_by_day.get(d, []),
            "blocks": blocks,
            "planned_minutes": sum(b["minutes"] for b in blocks),
            "beyond_horizon": bool(horizon_last and d > horizon_last),
        })

    return {"days": out, "start": first.isoformat(), "end": last.isoformat(),
            "horizon_end": horizon_last,
            "today": now.astimezone(tz).date().isoformat()}


# ------------------------------------------------------------- week ahead

def week_ahead(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    """Assignment due dates only — not individual steps."""
    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT a.id, a.title, a.due_at, a.kind, a.status,
               c.code AS class_code, c.color,
               (SELECT COUNT(*) FROM subtasks
                 WHERE assignment_id = a.id)                      AS total_n,
               (SELECT COUNT(*) FROM subtasks
                 WHERE assignment_id = a.id AND status != 'todo') AS done_n,
               (SELECT COALESCE(SUM(est_minutes),0) FROM subtasks
                 WHERE assignment_id = a.id AND status = 'todo')  AS left_min
          FROM assignments a
          LEFT JOIN classes c ON c.id = a.class_id
         WHERE a.status = 'todo' AND a.due_at IS NOT NULL AND a.due_at <= ?
         ORDER BY a.due_at""", (horizon,)).fetchall()

    buckets: dict[str, list] = {}
    for r in rows:
        key = _aware(r["due_at"]).astimezone().date().isoformat()
        buckets.setdefault(key, []).append({**dict(r), "color": r["color"] or "#6B7FD7"})

    today = now.astimezone().date()
    return [{
        "date": (today + timedelta(days=i)).isoformat(),
        "items": buckets.get((today + timedelta(days=i)).isoformat(), []),
    } for i in range(days)]
