"""Durable workspace objects and dataset freshness checks."""

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from app.auth import authz
from app.auth.authz import Action
from app.auth.deps import User
from app.contracts.outcomes import AppError, Outcome
from app.contracts.result import AnalysisResult


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_conversation(
    conn: sqlite3.Connection, user: User, title: str, dataset_ids: list[str]
) -> dict:
    """Create a conversation after checking ownership of every selected dataset."""
    for dataset_id in dict.fromkeys(dataset_ids):
        authz.require_owned(conn, "datasets", dataset_id, user, Action.read)
    conversation_id = uuid.uuid4().hex
    state = {"dataset_ids": list(dict.fromkeys(dataset_ids))}
    conn.execute(
        "INSERT INTO conversations (id, owner_id, title, state_json, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (conversation_id, user.id, title, json.dumps(state), _now()),
    )
    return {
        "id": conversation_id,
        "title": title,
        "state": state,
        "created_at": conn.execute(
            "SELECT created_at FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()[0],
    }


def get_conversation(conn: sqlite3.Connection, user: User, conversation_id: str) -> dict:
    row = authz.require_owned(conn, "conversations", conversation_id, user, Action.read)
    return {
        "id": row["id"],
        "title": row["title"],
        "state": json.loads(row["state_json"]),
        "created_at": row["created_at"],
    }


def save_result(conn: sqlite3.Connection, result: AnalysisResult) -> None:
    """Persist a completed result and its source versions as one durable record."""
    if result.meta is None:
        versions: dict[str, int] = {}
    else:
        versions = result.meta.dataset_versions
    conn.execute(
        "INSERT INTO results (id, owner_id, conversation_id, result_json,"
        " dataset_versions_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            result.id,
            result.owner_id,
            result.conversation_id,
            result.model_dump_json(),
            json.dumps(versions, sort_keys=True),
            result.created_at.isoformat(),
        ),
    )


def result_status(conn: sqlite3.Connection, user: User, result: AnalysisResult) -> str:
    """Return current/stale/missing after rechecking ownership of every live source."""
    authz.require_owned(conn, "results", result.id, user, Action.read)
    row = conn.execute(
        "SELECT dataset_versions_json FROM results WHERE id = ?", (result.id,)
    ).fetchone()
    if row is None:
        raise AppError(Outcome.not_found, "Result not found or you don't have access to it.")
    versions = json.loads(row["dataset_versions_json"])
    stale = False
    missing = False
    for dataset_id, version in versions.items():
        source = conn.execute(
            "SELECT id, owner_id, version FROM datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
        if source is None:
            missing = True
            continue
        # If a source was transferred or corrupted, never expose its metadata.
        authz.require_owned(conn, "datasets", dataset_id, user, Action.read)
        stale |= source["version"] != version
    if missing:
        return "missing"
    return "stale" if stale else "current"


def load_result(conn: sqlite3.Connection, user: User, result_id: str) -> AnalysisResult:
    row = authz.require_owned(conn, "results", result_id, user, Action.read)
    result = AnalysisResult.model_validate_json(row["result_json"])
    # Validate sources on every result read, even when the caller only needs the answer.
    result_status(conn, user, result)
    return result


def update_state(
    conn: sqlite3.Connection, user: User, conversation_id: str, state: dict[str, Any]
) -> None:
    authz.require_owned(conn, "conversations", conversation_id, user, Action.modify)
    for dataset_id in dict.fromkeys(state.get("dataset_ids", [])):
        authz.require_owned(conn, "datasets", dataset_id, user, Action.read)
    conn.execute(
        "UPDATE conversations SET state_json = ? WHERE id = ?", (json.dumps(state), conversation_id)
    )
