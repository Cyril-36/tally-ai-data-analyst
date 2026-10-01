"""Durable, idempotent turns with bounded concurrency and optional SSE progress."""

import json
import queue
import sqlite3
import threading
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent import service, tools
from app.agent.composer import compose
from app.auth import authz
from app.auth.authz import Action
from app.auth.deps import Conn, CurrentUser, StatusError, User
from app.config import get_settings
from app.contracts.events import Event, EventType
from app.contracts.outcomes import AppError, Outcome
from app.contracts.result import AnalysisResult, ChartSpec
from app.contracts.spec import AnalysisSpec
from app.db import connect, db_path
from app.workspace import store

router = APIRouter(prefix="/api/conversations", tags=["analysis"])


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    dataset_ids: list[str] | None = Field(default=None, max_length=20)
    request_key: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=128)


class AnalyzeBody(AskBody):
    spec: AnalysisSpec
    chart: ChartSpec | None = None
    anomaly_metric: str | None = None


def _begin(conn, user, conversation_id, body):
    payload = body.model_dump(mode="json")
    key = payload.pop("request_key")
    encoded = json.dumps(payload, sort_keys=True)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conversation = store.get_conversation(conn, user, conversation_id)
        previous = conn.execute(
            "SELECT * FROM turns WHERE conversation_id = ? AND request_key = ?",
            (conversation_id, key),
        ).fetchone()
        if previous:
            if previous["request_json"] != encoded:
                raise AppError(
                    Outcome.invalid_input, "This request key was used for another question."
                )
            if previous["result_id"]:
                saved = store.load_result(conn, user, previous["result_id"])
                if store.result_status(conn, user, saved) != "current":
                    raise StatusError(
                        Outcome.invalid_input,
                        "The previous result is stale. Use a new request key.",
                        409,
                    )
                conn.commit()
                return previous["id"], conversation["state"], saved
        now = time.time()
        # Recover a process crash; late completions cannot commit after their lease expires.
        conn.execute(
            "UPDATE turns SET status = 'failed' WHERE status = 'running' AND started_at < ?",
            (now - max(300, get_settings().llm_wall_s + 60),),
        )
        running = conn.execute(
            "SELECT count(*) FROM turns t JOIN conversations c "
            "ON c.id = t.conversation_id WHERE t.status = 'running' "
            "AND c.owner_id = ?",
            (user.id,),
        ).fetchone()[0]
        if running >= get_settings().max_user_active_turns:
            raise StatusError(
                Outcome.invalid_input, "Too many analyses are running. Try again shortly.", 429
            )
        global_running = conn.execute(
            "SELECT count(*) FROM turns WHERE status = 'running'"
        ).fetchone()[0]
        if global_running >= get_settings().max_active_turns:
            raise StatusError(
                Outcome.invalid_input, "Analysis capacity is busy. Try again shortly.", 429
            )
        state = conversation["state"]
        if body.dataset_ids is not None:
            state["dataset_ids"] = list(dict.fromkeys(body.dataset_ids))
        tools.build_catalog(conn, user, state.get("dataset_ids", []))
        turn_id = uuid.uuid4().hex
        if previous:
            if previous["status"] == "running":
                raise StatusError(Outcome.invalid_input, "This question is already running.", 409)
            conn.execute("DELETE FROM turns WHERE id = ?", (previous["id"],))
        try:
            conn.execute(
                "INSERT INTO turns VALUES (?, ?, ?, ?, NULL, 'running', ?)",
                (turn_id, conversation_id, key, encoded, now),
            )
        except sqlite3.IntegrityError:
            raise StatusError(
                Outcome.invalid_input,
                "Another question is running in this conversation. Try again shortly.",
                409,
            ) from None
        conn.commit()
        return turn_id, state, None
    except BaseException:
        conn.rollback()
        raise


def _finish(conn, user, turn_id, state, result, produced):
    conn.execute("BEGIN IMMEDIATE")
    try:
        turn = conn.execute(
            "SELECT * FROM turns WHERE id = ? AND status = 'running'", (turn_id,)
        ).fetchone()
        if turn is None:
            raise StatusError(Outcome.invalid_input, "This analysis is no longer active.", 409)
        authz.require_owned(conn, "conversations", result.conversation_id, user, Action.modify)
        for saved in produced:
            if saved.meta:
                tools.assert_versions(conn, user, saved.meta.dataset_versions)
            store.save_result(conn, saved)
        if result.spec:
            state["last_spec"] = result.spec.model_dump(mode="json")
        state["last_question"] = result.question
        state["last_result_id"] = result.id
        state["pending_clarification"] = (
            result.clarification.model_dump() if result.clarification else None
        )
        store.update_state(conn, user, result.conversation_id, state)
        conn.execute(
            "UPDATE turns SET status = 'done', result_id = ? WHERE id = ?", (result.id, turn_id)
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _execute(user: User, conversation_id: str, body: AskBody, turn_id: str, state: dict, emit):
    with connect(db_path()) as conn:
        try:
            if isinstance(body, AnalyzeBody):
                emit(
                    Event(
                        type=EventType.executing, data={"step": "Running the structured analysis"}
                    )
                )
                result = tools.analyze(
                    conn,
                    user,
                    conversation_id,
                    body.question,
                    body.spec,
                    state.get("dataset_ids", []),
                )
                if body.chart:
                    tools.set_chart(result, body.chart)
                if body.anomaly_metric:
                    tools.detect_result(result, body.anomaly_metric)
                emit(Event(type=EventType.validating, data={"step": "Checking result claims"}))
                result = compose(result)
                produced = [result]
            else:
                result, produced = service.run(
                    conn, user, conversation_id, body.question, state, emit
                )
            _finish(conn, user, turn_id, state, result, produced)
            return result
        except BaseException:
            conn.execute(
                "UPDATE turns SET status = 'failed' WHERE id = ? AND status = 'running'", (turn_id,)
            )
            raise


def _stream(user, conversation_id, body, turn_id, state, replay):
    if replay:
        yield Event(type=EventType.result, data=replay.model_dump(mode="json")).to_sse()
        return
    messages: queue.Queue = queue.Queue(maxsize=32)

    def work():
        try:
            result = _execute(user, conversation_id, body, turn_id, state, messages.put)
            messages.put(Event(type=EventType.result, data=result.model_dump(mode="json")))
        except AppError as exc:
            messages.put(
                Event(
                    type=EventType.error,
                    data={"outcome": exc.outcome.value, "message": exc.message},
                )
            )
        except Exception:
            messages.put(
                Event(
                    type=EventType.error,
                    data={
                        "outcome": "execution_failed",
                        "message": "Analysis failed. Retry the request.",
                    },
                )
            )

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    while True:
        try:
            event = messages.get(timeout=15)
        except queue.Empty:
            yield ": keep-alive\n\n"
            continue
        yield event.to_sse()
        if event.type in {EventType.result, EventType.error}:
            break


@router.post("/{conversation_id}/ask", response_model=AnalysisResult)
def ask(
    conversation_id: str,
    body: AskBody,
    request: Request,
    user: CurrentUser,
    conn: Conn,
    stream: bool = False,
):
    turn_id, state, replay = _begin(conn, user, conversation_id, body)
    if stream or "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(
            _stream(user, conversation_id, body, turn_id, state, replay),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return replay or _execute(user, conversation_id, body, turn_id, state, lambda event: None)


@router.post("/{conversation_id}/analyze", response_model=AnalysisResult)
def analyze(conversation_id: str, body: AnalyzeBody, user: CurrentUser, conn: Conn):
    """Deterministic analysis stays available when the AI provider is disabled."""
    turn_id, state, replay = _begin(conn, user, conversation_id, body)
    return replay or _execute(user, conversation_id, body, turn_id, state, lambda event: None)
