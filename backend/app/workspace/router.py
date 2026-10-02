"""Conversation, saved-result, chart, export, and dashboard endpoints."""

import sqlite3
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth import authz
from app.auth.authz import Action
from app.auth.deps import User, get_current_user
from app.contracts.outcomes import AppError, Outcome
from app.contracts.result import AnalysisResult, ChartSpec
from app.db import get_conn
from app.workspace import store
from app.workspace.overview import build_overview
from app.workspace.refresh import refresh_pin

router = APIRouter()
CurrentUser = Annotated[User, Depends(get_current_user)]
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationCreate(_Request):
    title: str = Field(min_length=1, max_length=200)
    dataset_ids: list[str] = Field(default_factory=list, max_length=20)


class ConversationUpdate(_Request):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    dataset_ids: list[str] | None = Field(default=None, max_length=20)


class ChartUpdate(_Request):
    chart: ChartSpec


class PinCreate(_Request):
    result_id: str
    idempotency_key: str | None = Field(default=None, max_length=200)


class PinUpdate(_Request):
    title: str | None = Field(default=None, max_length=200)
    position: int | None = Field(default=None, ge=0)


def _result_dict(conn: sqlite3.Connection, user: User, result: AnalysisResult) -> dict:
    return {
        "result": result.model_dump(mode="json"),
        "status": store.result_status(conn, user, result),
    }


def _load_json(row: sqlite3.Row) -> AnalysisResult:
    return AnalysisResult.model_validate_json(row["result_json"])


@router.get("/api/conversations")
def list_conversations(user: CurrentUser, conn: Conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id FROM conversations WHERE owner_id = ? ORDER BY created_at DESC", (user.id,)
    ).fetchall()
    return [store.get_conversation(conn, user, row["id"]) for row in rows]


@router.post("/api/conversations", status_code=201)
def create_conversation(body: ConversationCreate, user: CurrentUser, conn: Conn) -> dict:
    return store.create_conversation(conn, user, body.title, body.dataset_ids)


@router.get("/api/conversations/{conversation_id}")
def read_conversation(conversation_id: str, user: CurrentUser, conn: Conn) -> dict:
    return store.get_conversation(conn, user, conversation_id)


@router.patch("/api/conversations/{conversation_id}")
def update_conversation(
    conversation_id: str, body: ConversationUpdate, user: CurrentUser, conn: Conn
) -> dict:
    conn.execute("BEGIN IMMEDIATE")
    try:
        authz.require_owned(conn, "conversations", conversation_id, user, Action.modify)
        if body.dataset_ids is not None:
            _require_idle(conn, conversation_id)
            current = store.get_conversation(conn, user, conversation_id)["state"]
            selected = list(dict.fromkeys(body.dataset_ids))
            if selected != current.get("dataset_ids", []):
                store.update_state(conn, user, conversation_id, {"dataset_ids": selected})
        if body.title is not None:
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?", (body.title, conversation_id)
            )
        updated = store.get_conversation(conn, user, conversation_id)
        conn.commit()
        return updated
    except BaseException:
        conn.rollback()
        raise


@router.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, user: CurrentUser, conn: Conn) -> dict:
    conn.execute("BEGIN IMMEDIATE")
    try:
        authz.require_owned(conn, "conversations", conversation_id, user, Action.delete)
        _require_idle(conn, conversation_id)
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"deleted": conversation_id}


def _require_idle(conn: sqlite3.Connection, conversation_id: str) -> None:
    active = conn.execute(
        "SELECT 1 FROM turns WHERE conversation_id = ? AND status = 'running'", (conversation_id,)
    ).fetchone()
    if active:
        raise AppError(Outcome.invalid_input, "Wait for the current analysis to finish.")


@router.get("/api/conversations/{conversation_id}/results")
def list_conversation_results(conversation_id: str, user: CurrentUser, conn: Conn) -> list[dict]:
    authz.require_owned(conn, "conversations", conversation_id, user, Action.read)
    rows = conn.execute(
        "SELECT * FROM results WHERE conversation_id = ? AND owner_id = ? ORDER BY created_at",
        (conversation_id, user.id),
    ).fetchall()
    return [_result_dict(conn, user, _load_json(row)) for row in rows]


@router.get("/api/results/{result_id}")
def read_result(result_id: str, user: CurrentUser, conn: Conn) -> dict:
    result = store.load_result(conn, user, result_id)
    return _result_dict(conn, user, result)


@router.get("/api/results/{result_id}/chart")
def read_chart(result_id: str, user: CurrentUser, conn: Conn) -> dict:
    result = store.load_result(conn, user, result_id)
    from app.charts.builder import default_chart, figure_json

    if result.table is None:
        return {"data": [], "layout": {}}
    spec = result.chart or default_chart(result.table)
    if spec is None:
        return {"data": [], "layout": {}}
    return figure_json(result.table, spec)


@router.patch("/api/results/{result_id}/chart")
def update_chart(result_id: str, body: ChartUpdate, user: CurrentUser, conn: Conn) -> dict:
    result = store.load_result(conn, user, result_id)
    from app.agent.tools import set_chart
    from app.charts.builder import figure_json

    set_chart(result, body.chart)
    conn.execute(
        "UPDATE results SET result_json = ? WHERE id = ? AND owner_id = ?",
        (result.model_dump_json(), result_id, user.id),
    )
    return {
        "chart": result.chart.model_dump(mode="json"),
        "figure": figure_json(result.table, result.chart),
    }


def _csv_response(result: AnalysisResult, status: str) -> StreamingResponse:
    from app.reports.builder import csv_report

    return StreamingResponse(
        iter([csv_report(result)]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{result.id}.csv"',
            "X-Data-Status": status,
        },
    )


def _html_response(result: AnalysisResult, status: str) -> HTMLResponse:
    from app.reports.builder import html_report

    return HTMLResponse(html_report([result], statuses={result.id: status}))


@router.get("/api/results/{result_id}/export")
def export_result(result_id: str, user: CurrentUser, conn: Conn, format: str = "html"):
    result = store.load_result(conn, user, result_id)
    status = store.result_status(conn, user, result)
    if format == "csv":
        return _csv_response(result, status)
    if format == "html":
        return _html_response(result, status)
    raise AppError(Outcome.invalid_input, "format must be html or csv.")


@router.get("/api/conversations/{conversation_id}/export")
def export_conversation(conversation_id: str, user: CurrentUser, conn: Conn):
    authz.require_owned(conn, "conversations", conversation_id, user, Action.export)
    rows = conn.execute(
        "SELECT * FROM results WHERE conversation_id = ? AND owner_id = ? ORDER BY created_at",
        (conversation_id, user.id),
    ).fetchall()
    from app.reports.builder import html_report

    results = [_load_json(row) for row in rows]
    statuses = {result.id: store.result_status(conn, user, result) for result in results}
    return HTMLResponse(html_report(results, statuses=statuses))


@router.get("/api/dashboard")
def dashboard(user: CurrentUser, conn: Conn) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM pins WHERE owner_id = ? ORDER BY position, created_at", (user.id,)
    ).fetchall()
    result = []
    for row in rows:
        authz.require_owned(conn, "pins", row["id"], user, Action.read)
        saved = store.load_result(conn, user, row["result_id"])
        result.append(
            {
                "id": row["id"],
                "result_id": row["result_id"],
                "title": row["title"],
                "position": row["position"],
                "status": store.result_status(conn, user, saved),
                "result": saved.model_dump(mode="json"),
            }
        )
    return result


@router.post("/api/pins", status_code=201)
def create_pin(body: PinCreate, user: CurrentUser, conn: Conn) -> dict:
    pinned = store.load_result(conn, user, body.result_id)
    title = pinned.question.strip()[:200]  # a card is named by the question it answers
    # A retry with the same key returns its original pin only for the same result.
    if body.idempotency_key is not None:
        existing = conn.execute(
            "SELECT * FROM pins WHERE owner_id = ? AND idempotency_key = ?",
            (user.id, body.idempotency_key),
        ).fetchone()
        if existing:
            if existing["result_id"] != body.result_id:
                raise AppError(Outcome.invalid_input, "This pin key was used for another result.")
            return dict(existing)
    result = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM pins WHERE owner_id = ?", (user.id,)
    ).fetchone()[0]
    pin_id = uuid.uuid4().hex
    try:
        conn.execute(
            "INSERT INTO pins (id, owner_id, result_id, idempotency_key, created_at, title,"
            " position) VALUES (?, ?, ?, ?, datetime('now'), ?, ?)",
            (pin_id, user.id, body.result_id, body.idempotency_key, title, result),
        )
    except sqlite3.IntegrityError:
        if body.idempotency_key is None:
            raise
        existing = conn.execute(
            "SELECT * FROM pins WHERE owner_id = ? AND idempotency_key = ?",
            (user.id, body.idempotency_key),
        ).fetchone()
        if existing is None:
            raise
        if existing["result_id"] != body.result_id:
            raise AppError(
                Outcome.invalid_input, "This pin key was used for another result."
            ) from None
        return dict(existing)
    return dict(conn.execute("SELECT * FROM pins WHERE id = ?", (pin_id,)).fetchone())


@router.patch("/api/pins/{pin_id}")
def update_pin(pin_id: str, body: PinUpdate, user: CurrentUser, conn: Conn) -> dict:
    authz.require_owned(conn, "pins", pin_id, user, Action.modify)
    updates = {
        key: value
        for key, value in body.model_dump(exclude_unset=True).items()
        if value is not None
    }
    if updates:
        if "title" in updates:
            conn.execute("UPDATE pins SET title = ? WHERE id = ?", (updates["title"], pin_id))
        if "position" in updates:
            conn.execute("UPDATE pins SET position = ? WHERE id = ?", (updates["position"], pin_id))
    return dict(conn.execute("SELECT * FROM pins WHERE id = ?", (pin_id,)).fetchone())


@router.delete("/api/pins/{pin_id}")
def delete_pin(pin_id: str, user: CurrentUser, conn: Conn) -> dict:
    authz.require_owned(conn, "pins", pin_id, user, Action.delete)
    conn.execute("DELETE FROM pins WHERE id = ?", (pin_id,))
    return {"deleted": pin_id}


@router.post("/api/datasets/{dataset_id}/overview")
def dataset_overview(dataset_id: str, user: CurrentUser, conn: Conn) -> dict:
    """Automatic overview cards for one dataset; no AI involved (see workspace/overview.py)."""
    return build_overview(conn, user, dataset_id)


@router.post("/api/pins/{pin_id}/refresh")
def refresh_pinned(pin_id: str, user: CurrentUser, conn: Conn) -> dict:
    """Re-run the pin's stored calculation on current data; no AI involved."""
    return refresh_pin(conn, user, pin_id)
