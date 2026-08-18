"""The lifecycle of an individual step."""
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..models import SubtaskPatch
from ..services import scheduling

router = APIRouter(prefix="/api", tags=["subtasks"])


@router.patch("/subtasks/{sid}")
def patch_subtask(sid: int, body: SubtaskPatch,
                  conn: sqlite3.Connection = Depends(db.get_db)):
    row = conn.execute("SELECT * FROM subtasks WHERE id = ?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "subtask not found")

    if body.action == "done":
        # Capture the slot this step currently occupies, so the timeline can
        # turn that block green in place instead of drawing a new one.
        ps = pe = None
        try:
            plan = scheduling.plan_horizon(conn, settings=db.get_settings(conn))
            mine = [b for d in plan["days"] for b in d["blocks"]
                    if b["subtask_id"] == sid]
            if mine:
                ps = min(b["start"] for b in mine)
                pe = max(b["end"] for b in mine)
        except Exception:
            pass
        conn.execute("""UPDATE subtasks
                           SET status='done', completed_at=?,
                               planned_start=?, planned_end=?
                         WHERE id=?""", (db.utcnow(), ps, pe, sid))
    elif body.action == "undo":
        conn.execute("UPDATE subtasks SET status='todo', completed_at=NULL, "
                     "snoozed_until=NULL, planned_start=NULL, planned_end=NULL "
                     "WHERE id=?", (sid,))
    elif body.action == "skip":
        conn.execute("UPDATE subtasks SET status='skipped' WHERE id=?", (sid,))
    elif body.action == "snooze":
        until = datetime.now(timezone.utc) + timedelta(minutes=body.snooze_minutes)
        conn.execute("UPDATE subtasks SET snoozed_until=? WHERE id=?",
                     (until.isoformat(), sid))

    edits = {k: v for k, v in
             (("title", body.title), ("est_minutes", body.est_minutes)) if v is not None}
    if edits:
        sets = ", ".join(f"{k} = ?" for k in edits)
        conn.execute(f"UPDATE subtasks SET {sets} WHERE id = ?", (*edits.values(), sid))

    aid = row["assignment_id"]
    open_left = conn.execute(
        "SELECT COUNT(*) FROM subtasks WHERE assignment_id=? AND status='todo'",
        (aid,)).fetchone()[0]
    conn.execute("UPDATE assignments SET status=? WHERE id=?",
                 ("done" if open_left == 0 else "todo", aid))
    conn.commit()
    return {"ok": True, "assignment_complete": open_left == 0}


@router.delete("/subtasks/{sid}", status_code=204)
def delete_subtask(sid: int, conn: sqlite3.Connection = Depends(db.get_db)):
    conn.execute("DELETE FROM subtasks WHERE id = ?", (sid,))
    conn.commit()
