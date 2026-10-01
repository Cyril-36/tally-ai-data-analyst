"""Interface used by every router. ``get_current_user`` resolves the ``sid`` cookie."""

import sqlite3
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.auth import sessions
from app.contracts.outcomes import AppError, Outcome
from app.db import get_conn

COOKIE_NAME = "sid"

Conn = Annotated[sqlite3.Connection, Depends(get_conn)]  # request-scoped SQLite connection


@dataclass(frozen=True)
class User:
    id: str
    email: str
    role: str  # "analyst" | "admin"


class StatusError(AppError):
    """An AppError that needs a specific HTTP status (401, 429) the Outcome table lacks."""

    def __init__(self, outcome: Outcome, message: str, status: int):
        super().__init__(outcome, message)
        self.status = status


def get_current_user(request: Request, conn: Conn) -> User:
    sid = request.cookies.get(COOKIE_NAME)
    row = sessions.load_session_user(conn, sid) if sid else None
    if row is None:
        raise StatusError(Outcome.permission_denied, "Please sign in.", 401)
    request.state.user_id = row["id"]
    return User(id=row["id"], email=row["email"], role=row["role"])


# Annotated alias so route signatures stay short (and lint-clean).
CurrentUser = Annotated[User, Depends(get_current_user)]
