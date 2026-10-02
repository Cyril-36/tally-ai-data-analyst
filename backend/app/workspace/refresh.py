"""Re-run a pinned result's stored calculation on the current data.

The pin keeps the exact AnalysisSpec that produced it, so refreshing re-executes that spec on
the validated path instead of asking a model to reinterpret the question. It works with AI
switched off and cannot drift to a different calculation.
"""

import sqlite3
from contextlib import suppress

from app.agent import tools
from app.agent.composer import compose
from app.analysis.compiler import dataset_names
from app.auth.authz import Action, require_owned
from app.auth.deps import User
from app.contracts.outcomes import AppError, Outcome
from app.contracts.result import AnalysisResult, ColumnRole
from app.contracts.spec import AnalysisSpec
from app.workspace import store


def refresh_pin(conn: sqlite3.Connection, user: User, pin_id: str) -> dict:
    pin = require_owned(conn, "pins", pin_id, user, Action.modify)
    old = store.load_result(conn, user, pin["result_id"])
    if store.result_status(conn, user, old) == "current":
        return _item(conn, user, pin, old)  # nothing changed: a retry after success is a no-op
    if old.spec is None or old.meta is None:
        raise AppError(Outcome.unsupported, "This card has no stored calculation to re-run.")
    ids = list(old.meta.dataset_versions)
    current = {}
    for dataset_id in ids:
        row = conn.execute(
            "SELECT name FROM datasets WHERE id = ? AND owner_id = ?", (dataset_id, user.id)
        ).fetchone()
        if row is None:
            raise AppError(
                Outcome.not_found, "A source dataset was deleted, so this card can't be re-run."
            )
        current[dataset_id] = row["name"]
    spec = _with_current_names(old.spec, set(current.values()))

    result = tools.analyze(conn, user, old.conversation_id, old.question, spec, ids)
    if old.anomalies is not None:
        tools.detect_result(result, _anomaly_metric(old, result))
    if old.chart is not None:
        with suppress(AppError):  # keep the chart the user chose, if it still fits the rows
            tools.set_chart(result, old.chart)
    result = compose(result)
    # Save and repoint together, and only if the pin still shows the result this refresh
    # started from; a concurrent or retried refresh that already moved it wins, and this one
    # leaves no extra result behind.
    conn.execute("BEGIN IMMEDIATE")
    try:
        store.save_result(conn, result)
        moved = conn.execute(
            "UPDATE pins SET result_id = ? WHERE id = ? AND result_id = ?",
            (result.id, pin_id, old.id),
        ).rowcount
        conn.execute("COMMIT" if moved else "ROLLBACK")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    pin = require_owned(conn, "pins", pin_id, user, Action.read)
    return _item(conn, user, pin, result if moved else store.load_result(conn, user,
                                                                          pin["result_id"]))


def _item(conn: sqlite3.Connection, user: User, pin, result: AnalysisResult) -> dict:
    return {
        "id": pin["id"],
        "result_id": result.id,
        "title": pin["title"],
        "position": pin["position"],
        "status": store.result_status(conn, user, result),
        "result": result.model_dump(mode="json"),
    }


def _with_current_names(spec: AnalysisSpec, names: set[str]) -> AnalysisSpec:
    """A renamed single source is followed; renamed joined sources need the question again."""
    used = set(dataset_names(spec))
    if used == names:
        return spec
    if len(used) == 1 and len(names) == 1:
        return AnalysisSpec.model_validate(_rename(spec.model_dump(mode="json"), used.pop(),
                                                   next(iter(names))))
    raise AppError(
        Outcome.invalid_input,
        "A source dataset was renamed, so this card can't be re-run. Ask the question again.",
    )


def _rename(value, old: str, new: str):
    if isinstance(value, dict):
        return {k: (new if k in ("dataset", "base_dataset") and v == old else _rename(v, old, new))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_rename(v, old, new) for v in value]
    return value


def _anomaly_metric(old: AnalysisResult, new: AnalysisResult) -> str:
    """The metric the original check used. Older results did not record it; fall back to a
    flagged metric, then to the only metric. Guessing among several is refused."""
    report = old.anomalies
    if report.metric:
        return report.metric
    if report.flags:
        return report.flags[0].metric
    metrics = [c.name for c in new.table.columns if c.role == ColumnRole.metric]
    if len(metrics) == 1:
        return metrics[0]
    raise AppError(
        Outcome.invalid_input,
        "This card's anomaly check can't be repeated exactly. Ask the question again.",
    )
