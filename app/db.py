"""SQLite connection handling and schema."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH, UPLOADS  # noqa: F401  (re-exported for tests)

SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    id          INTEGER PRIMARY KEY,
    code        TEXT NOT NULL,
    name        TEXT NOT NULL,
    term        TEXT,
    color       TEXT DEFAULT '#6b7fd7',
    context     TEXT,
    topic_schedule TEXT,
    readings    TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS class_documents (
    id          INTEGER PRIMARY KEY,
    class_id    INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    doc_type    TEXT,
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id          INTEGER PRIMARY KEY,
    class_id    INTEGER REFERENCES classes(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'deliverable',
    description TEXT,
    due_at      TEXT,
    status      TEXT NOT NULL DEFAULT 'todo',
    source_path TEXT,
    confirmed   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subtasks (
    id            INTEGER PRIMARY KEY,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    title         TEXT NOT NULL,
    detail        TEXT,
    est_minutes   INTEGER NOT NULL DEFAULT 30,
    status        TEXT NOT NULL DEFAULT 'todo',
    completed_at  TEXT,
    snoozed_until TEXT,
    not_before    TEXT,
    planned_start TEXT,
    planned_end   TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    class_id   INTEGER REFERENCES classes(id) ON DELETE SET NULL,
    title      TEXT NOT NULL,
    start_at   TEXT NOT NULL,
    end_at     TEXT,
    location     TEXT,
    all_day      INTEGER NOT NULL DEFAULT 0,
    repeat_days  TEXT,
    repeat_until TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,
    target_id  INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS materials (
    id         INTEGER PRIMARY KEY,
    class_id   INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    reference  TEXT NOT NULL,      -- the reading as the syllabus names it
    item_kind  TEXT,               -- oer | public_domain | lending | alternative | article
    title      TEXT,
    url        TEXT,
    source     TEXT,
    note       TEXT,
    found_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_log (
    id             INTEGER PRIMARY KEY,
    at             TEXT NOT NULL,
    kind           TEXT NOT NULL,
    model          TEXT NOT NULL,
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write    INTEGER NOT NULL DEFAULT 0,
    cache_read     INTEGER NOT NULL DEFAULT 0,
    cost_usd       REAL NOT NULL DEFAULT 0,
    ok             INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subtask_open ON subtasks(assignment_id, status, seq);
CREATE INDEX IF NOT EXISTS idx_assign_due   ON assignments(due_at);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_at     ON usage_log(at);
CREATE INDEX IF NOT EXISTS idx_materials_cl ON materials(class_id);
"""


def utcnow() -> str:
    """Current time as an ISO 8601 UTC string. Use this everywhere."""
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    # check_same_thread=False: FastAPI can run a dependency's setup and its
    # teardown on different threadpool threads. Each connection is still used
    # by exactly one request at a time, so this is safe.
    conn = sqlite3.connect(DB_PATH, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def get_db():
    """FastAPI dependency. One connection per request, always closed."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


# Columns added after first release. Safe to re-run on an existing database.
MIGRATIONS = [
    ("events",   "repeat_days",    "TEXT"),
    ("events",   "repeat_until",   "TEXT"),
    ("subtasks", "not_before",     "TEXT"),   # step gated until material is taught
    ("subtasks", "planned_start",  "TEXT"),   # slot it held when completed
    ("subtasks", "planned_end",    "TEXT"),
    ("classes",  "topic_schedule", "TEXT"),   # JSON: [{date, topics}] from syllabus
    ("classes",  "readings",       "TEXT"),   # JSON: [{title, author, type, doi, isbn}]
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    conn = connect()
    # WAL is persistent — stored in the file header, set once.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()


def get_settings(conn) -> dict:
    out = {}
    for r in conn.execute("SELECT key, value FROM settings"):
        try:
            out[r["key"]] = int(r["value"])
        except ValueError:
            out[r["key"]] = r["value"]
    return out


def enqueue(conn: sqlite3.Connection, kind: str, target_id: int) -> int:
    ts = utcnow()
    cur = conn.execute(
        "INSERT INTO jobs (kind, target_id, created_at, updated_at) VALUES (?,?,?,?)",
        (kind, target_id, ts, ts))
    conn.commit()
    return cur.lastrowid
