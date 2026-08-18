"""Settings, cost accounting, and health."""
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from .. import config, db
from ..services import context as ctx_mod
from ..services import scheduling

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/settings")
def get_settings(conn: sqlite3.Connection = Depends(db.get_db)):
    return {**scheduling.DEFAULTS, **db.get_settings(conn)}


@router.put("/settings")
def put_settings(body: dict, conn: sqlite3.Connection = Depends(db.get_db)):
    allowed = set(scheduling.DEFAULTS) | {"layout"}
    for k, v in body.items():
        if k in allowed:
            conn.execute("INSERT INTO settings (key,value) VALUES (?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (k, str(v)))
    conn.commit()
    return {**scheduling.DEFAULTS, **db.get_settings(conn)}


@router.get("/calibration")
def get_calibration(conn: sqlite3.Connection = Depends(db.get_db)):
    """How your real pace compares to the estimates."""
    return ctx_mod.calibration(conn)


@router.get("/usage")
def get_usage(days: int = 30, conn: sqlite3.Connection = Depends(db.get_db)):
    """Local token/cost accounting, computed from each API response."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    day0 = now.astimezone().replace(hour=0, minute=0, second=0,
                                    microsecond=0).astimezone(timezone.utc).isoformat()
    week0 = (now - timedelta(days=7)).isoformat()
    month0 = (now - timedelta(days=30)).isoformat()

    def agg(sql, *params):
        r = conn.execute(sql, params).fetchone()
        return {"calls": r[0] or 0, "cost": round(r[1] or 0, 4),
                "input": r[2] or 0, "output": r[3] or 0}

    TOTALS = ("SELECT COUNT(*), SUM(cost_usd), SUM(input_tokens), SUM(output_tokens) "
              "FROM usage_log WHERE at >= ?")

    by_kind = [{"kind": r[0], "calls": r[1], "cost": round(r[2] or 0, 4)}
               for r in conn.execute(
        "SELECT kind, COUNT(*), SUM(cost_usd) FROM usage_log WHERE at >= ? "
        "GROUP BY kind ORDER BY SUM(cost_usd) DESC", (since,))]

    by_day = [{"date": r[0], "calls": r[1], "cost": round(r[2] or 0, 4)}
              for r in conn.execute(
        "SELECT substr(at,1,10), COUNT(*), SUM(cost_usd) FROM usage_log "
        "WHERE at >= ? GROUP BY 1 ORDER BY 1 DESC LIMIT 30", (since,))]

    recent = [dict(r) for r in conn.execute(
        "SELECT at, kind, model, input_tokens, output_tokens, cost_usd, ok "
        "FROM usage_log ORDER BY id DESC LIMIT 12")]

    failed = conn.execute(
        "SELECT COUNT(*), SUM(cost_usd) FROM usage_log WHERE ok=0 AND at >= ?",
        (month0,)).fetchone()

    return {
        "today": agg(TOTALS, day0),
        "week": agg(TOTALS, week0),
        "month": agg(TOTALS, month0),
        "all_time": agg("SELECT COUNT(*), SUM(cost_usd), SUM(input_tokens), "
                        "SUM(output_tokens) FROM usage_log WHERE at >= ?", "0"),
        "by_kind": by_kind,
        "by_day": by_day,
        "recent": recent,
        "wasted": {"calls": failed[0] or 0, "cost": round(failed[1] or 0, 4)},
        "note": "Estimated locally from each response's usage block. "
                "Authoritative billing lives in the Claude Console.",
    }


@router.get("/usage/remote")
def get_usage_remote(days: int = 7):
    """Anthropic's own Usage & Cost API. Needs an Admin API key, which is a
    different credential from ANTHROPIC_API_KEY and isn't available to every
    account type. Returns a clear reason when it can't be used."""
    import os
    import httpx
    key = os.environ.get("ANTHROPIC_ADMIN_KEY")
    if not key:
        return {"available": False,
                "reason": "ANTHROPIC_ADMIN_KEY not set in .env"}
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT00:00:00Z")
    try:
        r = httpx.get(
            "https://api.anthropic.com/v1/organizations/cost_report",
            params={"starting_at": start, "bucket_width": "1d"},
            headers={"anthropic-version": "2023-06-01", "x-api-key": key},
            timeout=20.0)
        if r.status_code != 200:
            return {"available": False,
                    "reason": f"HTTP {r.status_code}", "detail": r.text[:300]}
        return {"available": True, "data": r.json()}
    except Exception as e:
        return {"available": False, "reason": str(e)[:200]}
