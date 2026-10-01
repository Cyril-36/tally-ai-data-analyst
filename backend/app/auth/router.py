"""/api/auth: signup, login, logout, me, change-password."""

import re
import sqlite3
import time

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.auth import passwords, sessions
from app.auth.deps import COOKIE_NAME, Conn, CurrentUser, StatusError
from app.contracts.outcomes import AppError, Outcome

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_ATTEMPTS_PER_MINUTE = 5
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_INVALID_LOGIN = "Invalid email or password."


class SignupBody(BaseModel):  # unknown fields (e.g. "role") are ignored; role is always analyst
    email: str = Field(max_length=254)
    password: str = Field(min_length=passwords.MIN_PASSWORD_LENGTH, max_length=256)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:  # noqa: V107
        v = passwords.normalize_email(v)
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v


class LoginBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)  # no min length: short guesses fail like any other


class ChangePasswordBody(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=passwords.MIN_PASSWORD_LENGTH, max_length=256)


class UserOut(BaseModel):
    id: str
    email: str
    role: str


def _start_session(request: Request, response: Response, conn: sqlite3.Connection, user_id: str):
    sid = sessions.create_session(conn, user_id)
    response.set_cookie(
        COOKIE_NAME,
        sid,
        max_age=int(sessions.ABSOLUTE_TIMEOUT.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


def _check_rate_limit(request: Request, email: str) -> None:
    attempts: dict = request.app.state.login_attempts
    key = (request.client.host if request.client else "unknown", email)
    now = time.monotonic()
    recent = [t for t in attempts.get(key, []) if now - t < 60]
    if len(recent) >= LOGIN_ATTEMPTS_PER_MINUTE:
        attempts[key] = recent
        raise StatusError(
            Outcome.permission_denied, "Too many sign-in attempts. Try again in a minute.", 429
        )
    attempts[key] = [*recent, now]


@router.post("/signup", status_code=201)
def signup(
    body: SignupBody, request: Request, response: Response,
    conn: Conn,
) -> UserOut:
    user_id = passwords.create_user(conn, body.email, body.password, "analyst")
    _start_session(request, response, conn, user_id)
    return UserOut(id=user_id, email=body.email, role="analyst")


@router.post("/login")
def login(
    body: LoginBody, request: Request, response: Response,
    conn: Conn,
) -> UserOut:
    email = passwords.normalize_email(body.email)
    _check_rate_limit(request, email)
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row is None:
        passwords.burn_verification_time(body.password)
        raise StatusError(Outcome.permission_denied, _INVALID_LOGIN, 401)
    if not passwords.verify_password(row["password_hash"], body.password):
        raise StatusError(Outcome.permission_denied, _INVALID_LOGIN, 401)
    _start_session(request, response, conn, row["id"])
    return UserOut(id=row["id"], email=row["email"], role=row["role"])


@router.post("/logout", status_code=204)
def logout(
    request: Request, response: Response, conn: Conn
) -> None:
    sid = request.cookies.get(COOKIE_NAME)
    if sid:
        sessions.delete_session(conn, sid)
    response.delete_cookie(COOKIE_NAME)


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return UserOut(id=user.id, email=user.email, role=user.role)


@router.post("/change-password", status_code=204)
def change_password(
    body: ChangePasswordBody, request: Request, response: Response,
    user: CurrentUser, conn: Conn,
) -> None:
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()
    if not passwords.verify_password(row["password_hash"], body.current_password):
        raise AppError(Outcome.invalid_input, "Current password is incorrect.")
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (passwords.hash_password(body.new_password), user.id),
    )
    sessions.delete_user_sessions(conn, user.id)
    _start_session(request, response, conn, user.id)  # keep this browser signed in
