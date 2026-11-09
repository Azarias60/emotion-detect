import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DATABASE_PATH


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                gender TEXT DEFAULT '',
                age INTEGER,
                email TEXT DEFAULT '',
                user_pic TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                create_user_id INTEGER NOT NULL,
                report_type TEXT NOT NULL DEFAULT 'emotion',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                result_json TEXT NOT NULL DEFAULT '{}',
                report_text TEXT NOT NULL DEFAULT '',
                file_address TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(create_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_reports_user_time
                ON reports(create_user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_reports_user_type
                ON reports(create_user_id, report_type);
            """
        )


@contextmanager
def database():
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

