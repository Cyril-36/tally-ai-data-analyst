import json
import uuid
from datetime import UTC, datetime

import pytest

from app.auth import passwords
from app.auth.deps import User
from app.contracts.outcomes import AppError, Outcome
from app.contracts.result import (
    AnalysisResult,
    ColumnRole,
    ExecutionMeta,
    ResultColumn,
    ResultTable,
)
from app.workspace import store


def _user(conn, email: str) -> User:
    passwords.create_user(conn, email, "correct-horse-battery", "analyst")
    row = conn.execute("SELECT id, email, role FROM users WHERE email = ?", (email,)).fetchone()
    return User(**dict(row))


def _dataset(conn, owner_id: str, dataset_id: str, version: int = 1) -> None:
    conn.execute(
        "INSERT INTO datasets (id, owner_id, name, filename, version, row_count, profile_json,"
        " created_at, updated_at) VALUES (?, ?, ?, 'data.csv', ?, 1, '{}', 'now', 'now')",
        (dataset_id, owner_id, dataset_id, version),
    )


def _result(user: User, conversation_id: str, dataset_id: str) -> AnalysisResult:
    return AnalysisResult(
        id=uuid.uuid4().hex,
        owner_id=user.id,
        conversation_id=conversation_id,
        question="total by group",
        outcome=Outcome.ok,
        table=ResultTable(
            columns=[ResultColumn(name="total", dtype="BIGINT", role=ColumnRole.metric)], rows=[[3]]
        ),
        meta=ExecutionMeta(
            sql="SELECT 3",
            params=[],
            row_count=1,
            truncated=False,
            elapsed_ms=1,
            dataset_versions={dataset_id: 1},
        ),
        created_at=datetime.now(UTC),
    )


def test_conversation_round_trip_and_dataset_ownership(db):
    owner = _user(db, "owner@example.com")
    other = _user(db, "other@example.com")
    _dataset(db, owner.id, "owned-source")

    conversation = store.create_conversation(db, owner, "Analysis", ["owned-source"])
    assert store.get_conversation(db, owner, conversation["id"])["state"] == {
        "dataset_ids": ["owned-source"]
    }
    store.update_state(
        db, owner, conversation["id"], {"dataset_ids": ["owned-source"], "filter": "West"}
    )
    assert store.get_conversation(db, owner, conversation["id"])["state"]["filter"] == "West"
    with pytest.raises(AppError) as error:
        store.get_conversation(db, other, conversation["id"])
    assert error.value.outcome == Outcome.not_found
    with pytest.raises(AppError) as error:
        store.create_conversation(db, other, "Private source", ["owned-source"])
    assert error.value.outcome == Outcome.not_found


def test_result_load_and_freshness_transitions(db):
    owner = _user(db, "owner@example.com")
    other = _user(db, "other@example.com")
    dataset_id = "source"
    _dataset(db, owner.id, dataset_id)
    conversation = store.create_conversation(db, owner, "Analysis", [dataset_id])
    result = _result(owner, conversation["id"], dataset_id)
    store.save_result(db, result)

    assert store.load_result(db, owner, result.id).id == result.id
    assert store.result_status(db, owner, result) == "current"
    with pytest.raises(AppError) as error:
        store.load_result(db, other, result.id)
    assert error.value.outcome == Outcome.not_found

    db.execute("UPDATE datasets SET version = 2 WHERE id = ?", (dataset_id,))
    assert store.result_status(db, owner, result) == "stale"
    db.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
    assert store.result_status(db, owner, result) == "missing"
    # A saved answer remains retrievable with its missing-source status.
    assert store.load_result(db, owner, result.id).id == result.id


def test_idempotent_pin_and_ordering_are_exposed_by_schema(db):
    owner = _user(db, "owner@example.com")
    _dataset(db, owner.id, "source")
    conversation = store.create_conversation(db, owner, "Analysis", ["source"])
    result = _result(owner, conversation["id"], "source")
    store.save_result(db, result)

    # Pin routes exercise these columns; the migration must supply their defaults.
    columns = {row["name"] for row in db.execute("PRAGMA table_info(pins)")}
    assert {"title", "position"} <= columns
    assert json.loads(
        db.execute(
            "SELECT dataset_versions_json FROM results WHERE id = ?", (result.id,)
        ).fetchone()[0]
    ) == {"source": 1}
