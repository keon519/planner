"""Everything the displays read: now, today, the window, the week."""
import sqlite3

from fastapi import APIRouter, Depends

from .. import db
from ..services import ranking, scheduling

router = APIRouter(prefix="/api", tags=["plan"])


@router.get("/now")
def get_now(conn: sqlite3.Connection = Depends(db.get_db)):
    data = ranking.pick_now(conn)
    data["planning"] = conn.execute(
        """SELECT COUNT(*) FROM jobs
            WHERE status IN ('pending','running')
              AND kind IN ('extract','decompose')""").fetchone()[0]
    return data


@router.get("/agenda")
def get_agenda(limit: int = 6, conn: sqlite3.Connection = Depends(db.get_db)):
    return ranking.agenda(conn, limit)


@router.get("/day")
def get_day(conn: sqlite3.Connection = Depends(db.get_db)):
    """Today's timeline: events plus study blocks placed in the gaps."""
    return scheduling.plan_day(conn, settings=db.get_settings(conn))


@router.get("/window")
def get_window(back: int = 240, fwd: int = 300,
               conn: sqlite3.Connection = Depends(db.get_db)):
    """Rolling band around now — the scrolling timeline's data source."""
    return scheduling.window_view(conn, max(0, min(back, 720)),
                                max(60, min(fwd, 720)), db.get_settings(conn))


@router.get("/week")
def get_week(days: int = 7, conn: sqlite3.Connection = Depends(db.get_db)):
    """Assignment due dates for the week ahead, plus planned load per day."""
    weeks = scheduling.week_ahead(conn, days)
    horizon = scheduling.plan_horizon(conn, settings=db.get_settings(conn))
    load = {d["date"]: d["planned_minutes"] for d in horizon["days"]}
    for w in weeks:
        w["planned_minutes"] = load.get(w["date"], 0)
    return weeks


@router.get("/plan")
def get_plan(days: int = 14, conn: sqlite3.Connection = Depends(db.get_db)):
    """The full multi-day schedule."""
    return scheduling.plan_horizon(conn, settings=db.get_settings(conn), days=days)
