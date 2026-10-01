"""SQLite metadata store. One shared schema so parallel tracks don't diverge.
Datasets themselves are Parquet files under data_dir/datasets/<id>/v<version>.parquet."""

import fcntl
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('analyst','admin')), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL, filename TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
    row_count INTEGER NOT NULL, profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (owner_id, name)
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL, state_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id),
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    result_json TEXT NOT NULL, dataset_versions_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pins (
    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id),
    result_id TEXT NOT NULL REFERENCES results(id) ON DELETE CASCADE,
    idempotency_key TEXT, created_at TEXT NOT NULL,
    UNIQUE (owner_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
    result_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts (
    ip TEXT NOT NULL, email TEXT NOT NULL, attempted_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS login_attempt_time ON login_attempts(attempted_at);
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id)
      ON DELETE CASCADE, request_key TEXT NOT NULL, request_json TEXT NOT NULL,
    result_id TEXT REFERENCES results(id), status TEXT NOT NULL,
    started_at REAL NOT NULL, UNIQUE(conversation_id, request_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_turn ON turns(conversation_id)
    WHERE status = 'running';
"""


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # WAL mode cannot be changed while another worker initializes the same database.
    with path.with_suffix(".init.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with connect(path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            try:
                pin_columns = {row["name"] for row in conn.execute("PRAGMA table_info(pins)")}
                if "title" not in pin_columns:
                    conn.execute("ALTER TABLE pins ADD COLUMN title TEXT NOT NULL DEFAULT ''")
                if "position" not in pin_columns:
                    conn.execute("ALTER TABLE pins ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    # check_same_thread=False: FastAPI may open the connection and run the handler on
    # different threadpool threads. Each request still gets its own connection.
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def db_path() -> Path:
    from app.config import get_settings

    return get_settings().data_dir / "app.sqlite3"


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency. Tests override it or point DATA_DIR at a tmp dir."""
    with connect(db_path()) as conn:
        yield conn


def dataset_parquet_path(dataset_id: str, version: int) -> Path:
    from app.config import get_settings

    return get_settings().data_dir / "datasets" / dataset_id / f"v{version}.parquet"
