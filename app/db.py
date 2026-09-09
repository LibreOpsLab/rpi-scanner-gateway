"""
Tiny SQLite wrapper — single table, no ORM needed for this scale.
"""
import sqlite3
import time
from contextlib import contextmanager
from app.config import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received',
    -- statuses: received, ocr_running, uploading, done, failed
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    page_count INTEGER,
    blank_pages_removed INTEGER DEFAULT 0,
    original_size_bytes INTEGER,
    compressed_size_bytes INTEGER,
    thumbnail_path TEXT,
    archive_path TEXT,
    onedrive_link TEXT,
    email_sent INTEGER DEFAULT 0,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@contextmanager
def get_db():
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # avoid locking issues: watcher writes, dashboard reads
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)


def create_job(filename: str) -> int:
    now = time.time()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (filename, status, created_at, updated_at) VALUES (?, 'received', ?, ?)",
            (filename, now, now),
        )
        return cur.lastrowid


_JOB_COLUMNS = {
    "filename", "status", "created_at", "updated_at", "page_count",
    "blank_pages_removed", "original_size_bytes", "compressed_size_bytes",
    "thumbnail_path", "archive_path", "onedrive_link", "email_sent", "error_message",
}


def update_job(job_id: int, **fields):
    if not fields:
        return
    unknown = set(fields) - _JOB_COLUMNS
    if unknown:
        raise ValueError(f"update_job() got unknown column(s): {sorted(unknown)}")
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    with get_db() as conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", values)


def get_job(job_id: int):
    with get_db() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs(limit: int = 50):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


def list_failed_jobs():
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE status = 'failed' ORDER BY created_at DESC"
        ).fetchall()


def delete_job(job_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def get_latest_job():
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()


def get_in_flight_job():
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE status IN ('received', 'ocr_running', 'uploading') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()


def count_jobs_since(cutoff_timestamp: float) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE created_at >= ?", (cutoff_timestamp,)
        ).fetchone()
        return row["n"]


def email_send_counts_since(cutoff_timestamp: float) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT "
            "  SUM(CASE WHEN email_sent = 1 THEN 1 ELSE 0 END) AS sent, "
            "  SUM(CASE WHEN email_sent = 0 THEN 1 ELSE 0 END) AS not_sent "
            "FROM jobs WHERE status = 'done' AND created_at >= ?",
            (cutoff_timestamp,),
        ).fetchone()
        return {"sent": row["sent"] or 0, "not_sent": row["not_sent"] or 0}


def jobs_older_than(cutoff_timestamp: float):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE created_at < ? AND archive_path IS NOT NULL",
            (cutoff_timestamp,),
        ).fetchall()


def get_setting(key: str, default: str | None = None) -> str | None:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_recipient_email() -> str:
    """DB-stored override (settable from the dashboard's /settings page)
    takes precedence over config.RECIPIENT_EMAIL from .env."""
    return get_setting("recipient_email") or config.RECIPIENT_EMAIL
