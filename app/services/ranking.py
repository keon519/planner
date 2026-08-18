"""Decide the single next action.

Three rules:
  1. Only the first open step of each assignment is eligible.
  2. Rank by pressure = remaining_minutes / minutes_until_due.
  3. Ties break toward the shorter step.
"""
import re
import sqlite3
from datetime import datetime, timezone

# Assignments with no due date shouldn't outrank real deadlines, but
# shouldn't vanish either.
UNDATED_PRESSURE = 0.05

ELIGIBLE_SQL = """
SELECT s.id, s.title, s.detail, s.est_minutes,
       a.id AS assignment_id, a.title AS assignment, a.due_at, a.kind,
       c.code AS class_code, c.color,
       (SELECT COALESCE(SUM(est_minutes), 0) FROM subtasks
         WHERE assignment_id = a.id AND status = 'todo')     AS remaining,
       (SELECT COUNT(*) FROM subtasks
         WHERE assignment_id = a.id AND status != 'todo')    AS done_n,
       (SELECT COUNT(*) FROM subtasks
         WHERE assignment_id = a.id)                         AS total_n
  FROM subtasks s
  JOIN assignments a ON a.id = s.assignment_id
  LEFT JOIN classes c ON c.id = a.class_id
 WHERE a.status = 'todo'
   AND a.confirmed = 1
   AND s.status = 'todo'
   AND (s.snoozed_until IS NULL OR s.snoozed_until < :now)
   AND (s.not_before IS NULL OR s.not_before <= :now)
   AND s.seq = (SELECT MIN(seq) FROM subtasks
                 WHERE assignment_id = a.id AND status = 'todo')
"""

# Assignments whose next step is gated behind untaught material.
WAITING_SQL = """
SELECT a.id, a.title, c.code AS class_code, s.title AS step, s.not_before
  FROM assignments a
  JOIN subtasks s ON s.assignment_id = a.id
  LEFT JOIN classes c ON c.id = a.class_id
 WHERE a.status = 'todo' AND a.confirmed = 1
   AND s.status = 'todo' AND s.not_before > :now
   AND s.seq = (SELECT MIN(seq) FROM subtasks
                 WHERE assignment_id = a.id AND status = 'todo')
 ORDER BY s.not_before
"""

# Assignments that have no steps yet are invisible to the query above.
# Surface them so they don't silently disappear.
NEEDS_BREAKDOWN_SQL = """
SELECT a.id, a.title, a.due_at, c.code AS class_code
  FROM assignments a
  LEFT JOIN classes c ON c.id = a.class_id
 WHERE a.status = 'todo' AND a.confirmed = 1
   AND NOT EXISTS (SELECT 1 FROM subtasks WHERE assignment_id = a.id)
 ORDER BY COALESCE(a.due_at, '9999') ASC
"""


def _pressure(row: sqlite3.Row, now: datetime) -> float:
    if not row["due_at"]:
        return UNDATED_PRESSURE
    due = datetime.fromisoformat(row["due_at"])
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    minutes_left = (due - now).total_seconds() / 60
    if minutes_left <= 0:
        return float("inf")          # overdue always wins
    return row["remaining"] / minutes_left


def pick_now(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(ELIGIBLE_SQL, {"now": now.isoformat()}).fetchall()
    pending = conn.execute(NEEDS_BREAKDOWN_SQL).fetchall()
    needs_breakdown = [dict(r) for r in pending]
    waiting = [dict(r) for r in
               conn.execute(WAITING_SQL, {"now": now.isoformat()})]

    if not rows:
        return {"step": None, "needs_breakdown": needs_breakdown,
                "waiting": waiting}

    ranked = sorted(rows, key=lambda r: (-_pressure(r, now), r["est_minutes"]))
    top = ranked[0]
    p = _pressure(top, now)

    return {
        "step": {
            "subtask_id": top["id"],
            "title":      top["title"],
            "detail":     top["detail"],
            "minutes":    top["est_minutes"],
            "assignment": top["assignment"],
            "class_code": top["class_code"],
            "color":      top["color"] or "#6b7fd7",
            "due_at":     top["due_at"],
            "done_n":     top["done_n"],
            "total_n":    top["total_n"],
        },
        # pressure > 1 means more work remains than time exists
        "infeasible":      p > 1.0 and p != float("inf"),
        "overdue":         p == float("inf"),
        "remaining_min":   top["remaining"],
        "needs_breakdown": needs_breakdown,
        "waiting": waiting,
    }


def agenda(conn: sqlite3.Connection, limit: int = 6) -> list[dict]:
    rows = conn.execute("""
        SELECT a.id, a.title, a.due_at, a.kind, c.code AS class_code, c.color,
               (SELECT COUNT(*) FROM subtasks
                 WHERE assignment_id = a.id AND status != 'todo') AS done_n,
               (SELECT COUNT(*) FROM subtasks
                 WHERE assignment_id = a.id)                      AS total_n
          FROM assignments a
          LEFT JOIN classes c ON c.id = a.class_id
         WHERE a.status = 'todo' AND a.due_at IS NOT NULL
         ORDER BY a.due_at ASC LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


# ----------------------------------------------------------- recurrence

def _aware(iso: str):
    # A "+" in a query string decodes to a space, mangling "+00:00" offsets.
    iso = re.sub(r"\s(\d{2}:\d{2})$", r"+\1", iso.strip())
    d = datetime.fromisoformat(iso)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def expand_events(conn: sqlite3.Connection, start_iso: str, end_iso: str) -> list[dict]:
    """Weekly recurrence is expanded at read time, never materialised as rows.

    repeat_days uses 0=Sunday to match JavaScript's Date.getDay().
    """
    from datetime import timedelta

    start, end = _aware(start_iso), _aware(end_iso)
    out: list[dict] = []

    for r in conn.execute("""
            SELECT e.*, c.code AS class_code, c.color
              FROM events e LEFT JOIN classes c ON c.id = e.class_id"""):
        base = _aware(r["start_at"])
        dur = (_aware(r["end_at"]) - base) if r["end_at"] else None
        days_field = r["repeat_days"] if "repeat_days" in r.keys() else None

        if not days_field:
            if start <= base <= end:
                out.append({**dict(r), "occurrence_start": base.isoformat()})
            continue

        days = {int(d) for d in str(days_field).split(",") if d.strip().isdigit()}
        until = _aware(r["repeat_until"]) if r["repeat_until"] else end
        cursor = max(start, base)
        stop = min(end, until)

        day = cursor.date()
        while day <= stop.date():
            if (day.weekday() + 1) % 7 in days:      # 0 = Sunday
                occ = base.replace(year=day.year, month=day.month, day=day.day)
                if start <= occ <= stop:
                    out.append({
                        **dict(r),
                        "occurrence_start": occ.isoformat(),
                        "occurrence_end": (occ + dur).isoformat() if dur else None,
                        "is_occurrence": True,
                    })
            day += timedelta(days=1)

    out.sort(key=lambda x: x["occurrence_start"])
    return out
