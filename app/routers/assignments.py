"""Assignment CRUD, PDF upload, and planning context."""
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .. import db
from ..models import AssignmentIn, AssignmentPatch, SubtaskIn
from ..services import context as ctx_mod

router = APIRouter(prefix="/api", tags=["assignments"])


@router.get("/assignments")
def list_assignments(status: str | None = None,
                     conn: sqlite3.Connection = Depends(db.get_db)):
    sql = """SELECT a.*, c.code AS class_code, c.color,
                    (SELECT COUNT(*) FROM subtasks
                      WHERE assignment_id = a.id)                      AS total_n,
                    (SELECT COUNT(*) FROM subtasks
                      WHERE assignment_id = a.id AND status != 'todo') AS done_n,
                    (SELECT status FROM jobs WHERE target_id = a.id
                        AND kind IN ('extract','decompose')
                      ORDER BY id DESC LIMIT 1)                        AS job_status
               FROM assignments a
               LEFT JOIN classes c ON c.id = a.class_id"""
    params: tuple = ()
    if status:
        sql += " WHERE a.status = ?"
        params = (status,)
    sql += " ORDER BY COALESCE(a.due_at, '9999') ASC"
    return [dict(r) for r in conn.execute(sql, params)]


@router.post("/assignments", status_code=201)
def create_assignment(body: AssignmentIn,
                      conn: sqlite3.Connection = Depends(db.get_db)):
    cur = conn.execute(
        """INSERT INTO assignments
             (class_id, title, kind, description, due_at, created_at)
           VALUES (?,?,?,?,?,?)""",
        (body.class_id, body.title, body.kind, body.description,
         body.due_at, db.utcnow()))
    conn.commit()
    aid = cur.lastrowid
    if body.auto_plan:
        db.enqueue(conn, "decompose", aid)
    return {"id": aid, "planning": body.auto_plan}


@router.post("/assignments/upload", status_code=201)
def upload_assignment(file: UploadFile = File(...),
                      class_id: int | None = Form(None),
                      conn: sqlite3.Connection = Depends(db.get_db)):
    """Upload a PDF. Claude extracts the details, then plans the steps."""
    suffix = Path(file.filename).suffix.lower()
    if suffix != ".pdf":
        raise HTTPException(400, "only PDF files are supported")
    dest = db.UPLOADS / f"{uuid.uuid4().hex}.pdf"
    dest.write_bytes(file.file.read())

    cur = conn.execute(
        """INSERT INTO assignments
             (class_id, title, source_path, confirmed, created_at)
           VALUES (?,?,?,0,?)""",
        (class_id, Path(file.filename).stem[:80], str(dest), db.utcnow()))
    conn.commit()
    aid = cur.lastrowid
    db.enqueue(conn, "extract", aid)
    return {"id": aid, "queued": True}


@router.get("/assignments/{aid}")
def get_assignment(aid: int, conn: sqlite3.Connection = Depends(db.get_db)):
    row = conn.execute("SELECT * FROM assignments WHERE id = ?", (aid,)).fetchone()
    if not row:
        raise HTTPException(404, "assignment not found")
    subs = conn.execute(
        "SELECT * FROM subtasks WHERE assignment_id = ? ORDER BY seq", (aid,))
    job = conn.execute(
        """SELECT kind, status, last_error FROM jobs
            WHERE target_id = ? AND kind IN ('extract','decompose')
            ORDER BY id DESC LIMIT 1""", (aid,)).fetchone()
    return {**dict(row),
            "subtasks": [dict(s) for s in subs],
            "job": dict(job) if job else None}


@router.patch("/assignments/{aid}")
def patch_assignment(aid: int, body: AssignmentPatch,
                     conn: sqlite3.Connection = Depends(db.get_db)):
    fields = body.model_dump(exclude_none=True, exclude={"replan"})
    if "confirmed" in fields:
        fields["confirmed"] = int(fields["confirmed"])
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE assignments SET {sets} WHERE id = ?",
                     (*fields.values(), aid))
        conn.commit()
    if body.replan:
        db.enqueue(conn, "decompose", aid)
    return {"updated": bool(fields), "planning": body.replan}


@router.delete("/assignments/{aid}", status_code=204)
def delete_assignment(aid: int, conn: sqlite3.Connection = Depends(db.get_db)):
    conn.execute("DELETE FROM assignments WHERE id = ?", (aid,))
    conn.commit()


@router.post("/assignments/{aid}/subtasks", status_code=201)
def add_subtasks(aid: int, body: list[SubtaskIn],
                 conn: sqlite3.Connection = Depends(db.get_db)):
    if not conn.execute("SELECT 1 FROM assignments WHERE id = ?", (aid,)).fetchone():
        raise HTTPException(404, "assignment not found")
    start = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM subtasks WHERE assignment_id = ?", (aid,)
    ).fetchone()[0]
    conn.executemany(
        """INSERT INTO subtasks (assignment_id, seq, title, detail, est_minutes)
           VALUES (?,?,?,?,?)""",
        [(aid, start + i, s.title, s.detail, s.est_minutes)
         for i, s in enumerate(body, start=1)])
    conn.commit()
    return {"added": len(body)}


@router.get("/assignments/{aid}/context")
def get_context(aid: int, raw: bool = False,
                conn: sqlite3.Connection = Depends(db.get_db)):
    """Exactly what the decomposer is shown. Useful when steps look wrong."""
    data = ctx_mod.build(conn, aid)
    if not data:
        raise HTTPException(404, "assignment not found")
    return data if raw else {
        "prompt": ctx_mod.to_prompt(data),
        "siblings": len(data["siblings"]),
        "finished_steps": len(data["finished_steps"]),
        "competing": len(data["competing"]),
        "topic_dates": len(data["topic_schedule"]),
        "calibration": data["calibration"],
    }
