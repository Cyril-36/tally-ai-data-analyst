"""/api/admin: system settings. Admin role grants this and nothing else (no data access)."""

import sqlite3

from fastapi import APIRouter
from pydantic import BaseModel, Field, StrictBool

from app.auth import authz
from app.auth.deps import Conn, CurrentUser
from app.config import get_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


class SystemSettings(BaseModel):
    max_upload_mb: int = Field(ge=1, le=200)
    llm_enabled: StrictBool  # kill switch: False disables all LLM calls


def read_system_settings(conn: sqlite3.Connection) -> SystemSettings:
    """Stored values, falling back to defaults. Other tracks call this (e.g. upload limit)."""
    stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    return SystemSettings(
        max_upload_mb=int(stored.get("max_upload_mb", get_settings().max_upload_mb)),
        llm_enabled=stored.get("llm_enabled", "true") == "true",
    )


@router.get("/settings")
def get_system_settings(
    user: CurrentUser, conn: Conn
) -> SystemSettings:
    authz.require_admin(user)
    return read_system_settings(conn)


@router.put("/settings")
def put_system_settings(
    body: SystemSettings,
    user: CurrentUser, conn: Conn,
) -> SystemSettings:
    authz.require_admin(user)
    values = {"max_upload_mb": str(body.max_upload_mb),
              "llm_enabled": "true" if body.llm_enabled else "false"}
    for key, value in values.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    return body
