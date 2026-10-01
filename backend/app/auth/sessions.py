"""Server-side sessions. The cookie holds only a random id; everything else is in SQLite."""

import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

IDLE_TIMEOUT = timedelta(hours=8)
ABSOLUTE_TIMEOUT = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(UTC)


def create_session(conn: sqlite3.Connection, user_id: str) -> str:
    sid = secrets.token_urlsafe(32)
    now = _now().isoformat()
    conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
        (sid, user_id, now, now),
    )
    return sid


def load_session_user(conn: sqlite3.Connection, sid: str) -> sqlite3.Row | None:
    """Return the user row for a live session and refresh last_seen_at; None if unknown
    or expired (expired rows are deleted)."""
    row = conn.execute(
        "SELECT u.id, u.email, u.role, s.created_at, s.last_seen_at FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.id = ?",
        (sid,),
    ).fetchone()
    if row is None:
        return None
    now = _now()
    created = datetime.fromisoformat(row["created_at"])
    last_seen = datetime.fromisoformat(row["last_seen_at"])
    if now - created > ABSOLUTE_TIMEOUT or now - last_seen > IDLE_TIMEOUT:
        delete_session(conn, sid)
        return None
    conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (now.isoformat(), sid))
    return row


def delete_session(conn: sqlite3.Connection, sid: str) -> None:
    conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))


def delete_user_sessions(conn: sqlite3.Connection, user_id: str) -> None:
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
