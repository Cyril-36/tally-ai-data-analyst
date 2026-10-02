"""/api/admin: system settings. Admin role grants this and nothing else (no data access)."""

import sqlite3
import time
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StrictBool

from app.agent import provider
from app.auth import authz
from app.auth.deps import Conn, CurrentUser, StatusError
from app.config import get_settings
from app.contracts.outcomes import AppError, Outcome
from app.workspace import store


def _require_admin(user: CurrentUser) -> None:
    # A dependency runs before body validation, so non-admins are refused
    # without learning anything about the settings schema.
    authz.require_admin(user)


router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(_require_admin)])


class SystemSettings(BaseModel):
    max_upload_mb: int = Field(ge=1, le=200)
    llm_enabled: StrictBool  # kill switch: False disables all LLM calls
    sample_data_enabled: StrictBool = True  # False: POST /api/datasets/sample is refused
    retention_days: Literal[30, 90, 365] | None = 90  # conversations idle longer are purged


class SystemSettingsUpdate(SystemSettings):
    """PUT body. The two newer fields may be omitted, which keeps the stored value
    (``retention_days: null`` is explicit and means never purge)."""

    sample_data_enabled: StrictBool | None = None


class SystemSettingsSaved(SystemSettings):
    purged_conversations: int


def read_system_settings(conn: sqlite3.Connection) -> SystemSettings:
    """Stored values, falling back to defaults. Other tracks call this (e.g. upload limit)."""
    stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    return SystemSettings(
        max_upload_mb=int(stored.get("max_upload_mb", get_settings().max_upload_mb)),
        llm_enabled=stored.get("llm_enabled", "true") == "true",
        sample_data_enabled=stored.get("sample_data_enabled", "true") == "true",
        retention_days=_parse_retention(stored.get("retention_days")),
    )


def _parse_retention(stored: str | None) -> int | None:
    if stored is None:
        return 90
    return None if stored == "never" else int(stored)


@router.get("/settings")
def get_system_settings(conn: Conn) -> SystemSettings:
    return read_system_settings(conn)


@router.put("/settings")
def put_system_settings(
    body: SystemSettingsUpdate,
    conn: Conn,
) -> SystemSettingsSaved:
    current = read_system_settings(conn)
    sample = current.sample_data_enabled if body.sample_data_enabled is None \
        else body.sample_data_enabled
    retention = body.retention_days if "retention_days" in body.model_fields_set \
        else current.retention_days
    values = {"max_upload_mb": str(body.max_upload_mb),
              "llm_enabled": "true" if body.llm_enabled else "false",
              "sample_data_enabled": "true" if sample else "false",
              "retention_days": "never" if retention is None else str(retention)}
    for key, value in values.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    purged = store.purge_old_conversations(conn, retention)
    return SystemSettingsSaved(max_upload_mb=body.max_upload_mb, llm_enabled=body.llm_enabled,
                               sample_data_enabled=sample, retention_days=retention,
                               purged_conversations=purged)


@router.post("/provider-test")
def test_provider(conn: Conn) -> dict:
    """One minimal provider call. Failures never include provider text, keys or URLs."""
    if not read_system_settings(conn).llm_enabled:
        raise StatusError(Outcome.invalid_input,
                          "AI is turned off; turn it on to test the provider.", 409)
    started = time.perf_counter()
    try:
        model = provider.get_provider().ping()
    except AppError:
        raise AppError(Outcome.provider_failed,
                       "The AI provider did not respond correctly.") from None
    return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000),
            "model": model}
