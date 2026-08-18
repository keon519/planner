"""Background job loop. Runs as an asyncio task inside uvicorn.

Jobs are rows in the `jobs` table, so a restart mid-flight loses nothing —
anything still 'running' is reset to 'pending' on startup.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from .. import ai, db
from . import context, materials

log = logging.getLogger("worker")

POLL_SECONDS = 4
MAX_ATTEMPTS = 3


def _log_usage(conn, sink, ok=True):
    for u in sink:
        conn.execute(
            """INSERT INTO usage_log (at, kind, model, input_tokens, output_tokens,
                                      cache_write, cache_read, cost_usd, ok)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (db.utcnow(), u["kind"], u["model"], u["input_tokens"],
             u["output_tokens"], u["cache_write"], u["cache_read"],
             u["cost_usd"], int(ok)))
    conn.commit()
    sink.clear()


def _set(conn, job_id, status, error=None):
    conn.execute(
        "UPDATE jobs SET status=?, last_error=?, updated_at=? WHERE id=?",
        (status, error, db.utcnow(), job_id))
    conn.commit()


def _hours_until(due_at: str | None) -> float | None:
    if not due_at:
        return None
    due = datetime.fromisoformat(due_at)
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    hrs = (due - datetime.now(timezone.utc)).total_seconds() / 3600
    return max(hrs, 0.0)


# --------------------------------------------------------------- handlers

def _do_extract(conn, aid: int, sink=None) -> None:
    row = conn.execute("SELECT * FROM assignments WHERE id=?", (aid,)).fetchone()
    if not row:
        return
    if row["source_path"]:
        data = ai.extract_pdf(row["source_path"], usage_sink=sink)
    else:
        data = ai.extract_text(row["description"] or row["title"], usage_sink=sink)

    due = data.get("due_at")
    if due and len(due) == 19:          # naive local time from the model
        due = datetime.fromisoformat(due).astimezone().astimezone(timezone.utc).isoformat()

    conn.execute(
        """UPDATE assignments
              SET title=?, kind=?, due_at=COALESCE(?, due_at), description=?
            WHERE id=?""",
        (data.get("title") or row["title"],
         data.get("kind") or "deliverable",
         due,
         data.get("description") or row["description"],
         aid))
    conn.commit()


def _do_decompose(conn, aid: int, sink=None) -> None:
    row = conn.execute("SELECT * FROM assignments WHERE id=?", (aid,)).fetchone()
    if not row:
        return
    ctx, topic_schedule = None, None
    if row["class_id"]:
        c = conn.execute(
            "SELECT code, name, context, topic_schedule FROM classes WHERE id=?",
            (row["class_id"],)).fetchone()
        if c:
            ctx = f"{c['code']} — {c['name']}" + (f"\n{c['context']}" if c["context"] else "")
            if c["topic_schedule"]:
                try:
                    topic_schedule = json.loads(c["topic_schedule"])
                except (ValueError, TypeError):
                    topic_schedule = None

    portfolio = None
    try:
        portfolio = context.to_prompt(context.build(conn, aid)) or None
    except Exception:
        log.exception("context build failed; planning without it")

    steps = ai.decompose(dict(row), ctx, _hours_until(row["due_at"]),
                         topic_schedule=topic_schedule, usage_sink=sink,
                         portfolio=portfolio)
    if not steps:
        raise RuntimeError("model returned no steps")

    # Replace any existing open steps; keep completed ones.
    conn.execute("DELETE FROM subtasks WHERE assignment_id=? AND status='todo'", (aid,))
    base = conn.execute(
        "SELECT COALESCE(MAX(seq),0) FROM subtasks WHERE assignment_id=?", (aid,)
    ).fetchone()[0]
    def _nb_iso(s):
        nb = s.get("not_before")
        if not nb:
            return None
        # Gate lifts at the start of that local day.
        local = datetime.strptime(nb, "%Y-%m-%d").astimezone()
        return local.astimezone(timezone.utc).isoformat()

    conn.executemany(
        """INSERT INTO subtasks
             (assignment_id, seq, title, detail, est_minutes, not_before)
           VALUES (?,?,?,?,?,?)""",
        [(aid, base + i, s["title"], s["detail"], s["est_minutes"], _nb_iso(s))
         for i, s in enumerate(steps, start=1)])
    conn.commit()


def _do_summarize(conn, doc_id: int, sink=None) -> None:
    d = conn.execute("SELECT * FROM class_documents WHERE id=?", (doc_id,)).fetchone()
    if not d:
        return
    out = ai.summarize_class_doc(d["stored_path"], usage_sink=sink)
    summary, sched = out.get("summary", ""), out.get("schedule", [])
    reads = out.get("readings", [])
    prev = conn.execute("SELECT context FROM classes WHERE id=?",
                        (d["class_id"],)).fetchone()["context"]
    merged = f"{prev}\n\n{summary}".strip() if prev else summary
    conn.execute("""UPDATE classes SET context=?, topic_schedule=?, readings=?
                     WHERE id=?""",
                 (merged[:4000], json.dumps(sched) if sched else None,
                  json.dumps(reads) if reads else None, d["class_id"]))
    conn.commit()
    if reads:
        db.enqueue(conn, "find_materials", d["class_id"])


def _do_find_materials(conn, class_id: int, sink=None) -> None:
    """Look for legally free copies of each reading the syllabus names."""
    row = conn.execute("SELECT readings FROM classes WHERE id=?",
                       (class_id,)).fetchone()
    if not row or not row["readings"]:
        return
    try:
        reads = json.loads(row["readings"])
    except (ValueError, TypeError):
        return

    for r in reads[:20]:
        ref = r.get("title", "").strip()
        if not ref:
            continue
        try:
            if r.get("type") == "article" or r.get("doi"):
                hit = materials.find_article(ref, r.get("doi"))
                materials.save(conn, class_id, "article", ref, [hit] if hit else [])
            else:
                found, resp = materials.find_book(ref, r.get("author"), r.get("isbn"))
                ai._record(sink, "find_materials", resp)
                materials.save(conn, class_id, "oer", ref, found)
        except Exception:
            log.exception("material lookup failed for %r", ref)
        if sink is not None:
            _log_usage(conn, sink, ok=True)


HANDLERS = {
    "extract":        _do_extract,
    "decompose":      _do_decompose,
    "summarize":      _do_summarize,
    "find_materials": _do_find_materials,
}


# ------------------------------------------------------------------ loop

def _run_one(job) -> None:
    """Blocking. Called via asyncio.to_thread so the event loop stays free."""
    conn = db.connect()
    sink: list = []
    try:
        HANDLERS[job["kind"]](conn, job["target_id"], sink)
        _log_usage(conn, sink, ok=True)
        _set(conn, job["id"], "done")
        # Extraction always chains into decomposition.
        if job["kind"] == "extract":
            db.enqueue(conn, "decompose", job["target_id"])
    except Exception as e:
        # Tokens are billed even when the job fails downstream, so log them.
        try:
            _log_usage(conn, sink, ok=False)
        except Exception:
            pass
        log.exception("job %s (%s) failed", job["id"], job["kind"])
        attempts = job["attempts"] + 1
        conn.execute("UPDATE jobs SET attempts=? WHERE id=?", (attempts, job["id"]))
        _set(conn, job["id"], "pending" if attempts < MAX_ATTEMPTS else "failed",
             str(e)[:500])
    finally:
        conn.close()


async def loop() -> None:
    # Anything left 'running' died with a previous process. Retry it.
    conn = db.connect()
    conn.execute("UPDATE jobs SET status='pending' WHERE status='running'")
    conn.commit()
    conn.close()

    while True:
        try:
            conn = db.connect()
            job = conn.execute(
                """SELECT * FROM jobs WHERE status='pending'
                   ORDER BY created_at LIMIT 1""").fetchone()
            if job:
                _set(conn, job["id"], "running")
            conn.close()

            if job:
                await asyncio.to_thread(_run_one, job)
                continue          # drain the queue without sleeping
        except Exception:
            log.exception("worker loop error")

        await asyncio.sleep(POLL_SECONDS)
