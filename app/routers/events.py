"""Calendar events, including weekly recurrence."""
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from .. import db
from ..models import EventIn, EventPatch
from ..services import ranking

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events")
def list_events(start: str | None = None, end: str | None = None,
                conn: sqlite3.Connection = Depends(db.get_db)):
    """Recurring events are expanded into occurrences across the window."""
    now = datetime.now(timezone.utc)
    start = start or now.isoformat()
    end = end or (now + timedelta(days=14)).isoformat()
    return ranking.expand_events(conn, start, end)


@router.get("/events/series")
def list_series(conn: sqlite3.Connection = Depends(db.get_db)):
    """The stored rows themselves, for editing and deleting."""
    return [dict(r) for r in conn.execute("""
        SELECT e.*, c.code AS class_code, c.color
          FROM events e LEFT JOIN classes c ON c.id = e.class_id
         ORDER BY e.start_at""")]


@router.post("/events", status_code=201)
def create_event(body: EventIn, conn: sqlite3.Connection = Depends(db.get_db)):
    cur = conn.execute(
        """INSERT INTO events
             (class_id, title, start_at, end_at, location, all_day,
              repeat_days, repeat_until)
           VALUES (?,?,?,?,?,?,?,?)""",
        (body.class_id, body.title, body.start_at, body.end_at, body.location,
         int(body.all_day), body.repeat_days or None, body.repeat_until))
    conn.commit()
    return {"id": cur.lastrowid}


@router.patch("/events/{eid}")
def patch_event(eid: int, body: EventPatch,
                conn: sqlite3.Connection = Depends(db.get_db)):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return {"updated": 0}
    sets = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(f"UPDATE events SET {sets} WHERE id = ?",
                       (*fields.values(), eid))
    conn.commit()
    return {"updated": cur.rowcount}


@router.delete("/events/{eid}", status_code=204)
def delete_event(eid: int, conn: sqlite3.Connection = Depends(db.get_db)):
    conn.execute("DELETE FROM events WHERE id = ?", (eid,))
    conn.commit()
