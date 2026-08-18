"""Courses, their uploaded documents, and free-reading lookup."""
import json
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .. import db
from ..models import ClassIn, ClassPatch
from ..services import materials as mat

router = APIRouter(prefix="/api", tags=["classes"])


@router.get("/classes")
def list_classes(conn: sqlite3.Connection = Depends(db.get_db)):
    return [dict(r) for r in conn.execute("SELECT * FROM classes ORDER BY code")]


@router.post("/classes", status_code=201)
def create_class(body: ClassIn, conn: sqlite3.Connection = Depends(db.get_db)):
    cur = conn.execute(
        """INSERT INTO classes (code, name, term, color, context, created_at)
           VALUES (?,?,?,?,?,?)""",
        (body.code, body.name, body.term, body.color, body.context, db.utcnow()))
    conn.commit()
    return {"id": cur.lastrowid}


@router.patch("/classes/{cid}")
def patch_class(cid: int, body: ClassPatch,
                conn: sqlite3.Connection = Depends(db.get_db)):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        return {"updated": 0}
    sets = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(f"UPDATE classes SET {sets} WHERE id = ?",
                       (*fields.values(), cid))
    conn.commit()
    return {"updated": cur.rowcount}


@router.get("/classes/{cid}")
def get_class(cid: int, conn: sqlite3.Connection = Depends(db.get_db)):
    row = conn.execute("SELECT * FROM classes WHERE id = ?", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "class not found")
    docs = conn.execute(
        "SELECT id, filename, doc_type, uploaded_at FROM class_documents "
        "WHERE class_id = ? ORDER BY uploaded_at DESC", (cid,))
    n = conn.execute("SELECT COUNT(*) FROM assignments WHERE class_id = ?",
                     (cid,)).fetchone()[0]
    return {**dict(row), "documents": [dict(d) for d in docs], "assignment_count": n}


@router.delete("/classes/{cid}", status_code=204)
def delete_class(cid: int, conn: sqlite3.Connection = Depends(db.get_db)):
    conn.execute("DELETE FROM classes WHERE id = ?", (cid,))
    conn.commit()


@router.post("/classes/{cid}/documents", status_code=201)
def upload_class_doc(cid: int, file: UploadFile = File(...),
                     doc_type: str = Form("syllabus"),
                     conn: sqlite3.Connection = Depends(db.get_db)):
    """Syllabus upload. Summarised into class context for better step planning."""
    if not conn.execute("SELECT 1 FROM classes WHERE id=?", (cid,)).fetchone():
        raise HTTPException(404, "class not found")
    dest = db.UPLOADS / f"{uuid.uuid4().hex}{Path(file.filename).suffix}"
    dest.write_bytes(file.file.read())
    cur = conn.execute(
        """INSERT INTO class_documents
             (class_id, filename, stored_path, doc_type, uploaded_at)
           VALUES (?,?,?,?,?)""",
        (cid, file.filename, str(dest), doc_type, db.utcnow()))
    conn.commit()
    db.enqueue(conn, "summarize", cur.lastrowid)
    return {"id": cur.lastrowid, "queued": True}


@router.delete("/classes/{cid}/documents/{doc_id}", status_code=204)
def delete_class_doc(cid: int, doc_id: int,
                     conn: sqlite3.Connection = Depends(db.get_db)):
    conn.execute("DELETE FROM class_documents WHERE id = ? AND class_id = ?",
                 (doc_id, cid))
    conn.commit()


@router.get("/classes/{cid}/materials")
def get_materials(cid: int, conn: sqlite3.Connection = Depends(db.get_db)):
    """Legally free copies found for this class's readings."""
    rows = conn.execute(
        "SELECT * FROM materials WHERE class_id = ? ORDER BY reference, id", (cid,))
    grouped: dict = {}
    for r in rows:
        grouped.setdefault(r["reference"], []).append(dict(r))
    row = conn.execute("SELECT readings FROM classes WHERE id=?", (cid,)).fetchone()
    reads = []
    if row and row["readings"]:
        try:
            reads = json.loads(row["readings"])
        except (ValueError, TypeError):
            reads = []
    return {
        "readings": reads,
        "found": grouped,
        "searched": len(grouped),
        "with_results": sum(1 for v in grouped.values() if v),
    }


@router.post("/classes/{cid}/materials/search", status_code=202)
def search_materials(cid: int, conn: sqlite3.Connection = Depends(db.get_db)):
    if not conn.execute("SELECT 1 FROM classes WHERE id=?", (cid,)).fetchone():
        raise HTTPException(404, "class not found")
    db.enqueue(conn, "find_materials", cid)
    return {"queued": True}
