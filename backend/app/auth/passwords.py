"""Password hashing (Argon2id) and user creation."""

import sqlite3
import uuid
from datetime import UTC, datetime
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.contracts.outcomes import AppError, Outcome

MIN_PASSWORD_LENGTH = 10
_hasher = PasswordHasher()  # argon2-cffi defaults to Argon2id


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@lru_cache
def _dummy_hash() -> str:
    return hash_password("not-a-real-password")


def burn_verification_time(password: str) -> None:
    """Hash-check against a dummy so unknown emails take as long as wrong passwords."""
    verify_password(_dummy_hash(), password)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def user_exists(conn: sqlite3.Connection, email: str) -> bool:
    row = conn.execute("SELECT 1 FROM users WHERE email = ?", (normalize_email(email),))
    return row.fetchone() is not None


def create_user(conn: sqlite3.Connection, email: str, password: str, role: str) -> str:
    """Insert a user and return its id. Raises AppError(invalid_input) for bad input."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AppError(
            Outcome.invalid_input, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if user_exists(conn, email):
        raise AppError(Outcome.invalid_input, "That email is already registered.")
    user_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO users (id, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, normalize_email(email), hash_password(password), role,
         datetime.now(UTC).isoformat()),
    )
    return user_id


def ensure_admin(conn: sqlite3.Connection, email: str, password: str) -> None:
    """Startup seed: create the admin only if that email does not exist yet."""
    if email and password and not user_exists(conn, email):
        create_user(conn, email, password, "admin")
