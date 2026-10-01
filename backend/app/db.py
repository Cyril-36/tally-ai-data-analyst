"""SQLite metadata store. One shared schema so parallel tracks don't diverge.
Datasets themselves are Parquet files under data_dir/datasets/<id>/v<version>.parquet."""

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
"""


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
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
