"""Automatic overview dashboard for one dataset.

Cards are chosen from the dataset profile (column roles and cardinality), never from column
names or a model, so the same rules apply to any upload and the overview works with AI off.
Every card is an ordinary stored AnalysisResult: computed by the sandboxed executor, narrated
by validated claims, pinnable, and marked out of date like any other result.
"""

import json
import re
import sqlite3
from datetime import date

from app.agent import tools
from app.agent.composer import compose
from app.auth.authz import Action, require_owned
from app.auth.deps import User
from app.contracts.outcomes import AppError
from app.contracts.result import ColumnRole
from app.contracts.spec import AnalysisSpec
from app.workspace import store

_MAX_GROUPS = 25  # a dimension with more distinct values is not a useful breakdown
_TOP_N = 10


def build_overview(conn: sqlite3.Connection, user: User, dataset_id: str) -> dict:
    row = require_owned(conn, "datasets", dataset_id, user, Action.analyze)
    conversation = _overview_conversation(conn, user, row)
    saved = conversation["state"].get("overview", {})
    if saved.get("version") == row["version"] and saved.get("result_ids"):
        cards = [_card(conn, user, kind, rid) for kind, rid in saved["result_ids"]]
        # Reuse only while every card still matches the data (a currency change keeps the
        # version but makes the cards out of date, so they are rebuilt).
        if all(card["status"] == "current" for card in cards):
            return _response(row, conversation["id"], cards, saved.get("skipped", []), reused=True)

    plan, skipped = plan_cards(row["name"], json.loads(row["profile_json"]))
    produced = []
    for kind, title, spec in plan:
        try:
            result = tools.analyze(conn, user, conversation["id"], title, spec, [dataset_id])
            if kind == "trend":
                metric = next(c.name for c in result.table.columns if c.role == ColumnRole.metric)
                tools.detect_result(result, metric)
            result = compose(result)
        except AppError as exc:
            skipped.append({"kind": kind, "reason": exc.message})
            continue
        store.save_result(conn, result)
        produced.append((kind, result.id))
    state = {**conversation["state"], "overview": {
        "dataset_id": dataset_id, "version": row["version"],
        "result_ids": produced, "skipped": skipped,
    }}
    store.update_state(conn, user, conversation["id"], state)
    cards = [_card(conn, user, kind, rid) for kind, rid in produced]
    return _response(row, conversation["id"], cards, skipped, reused=False)


def plan_cards(name: str, profile: dict) -> tuple[list[tuple[str, str, AnalysisSpec]], list[dict]]:
    """(kind, title, spec) for each card the data supports, plus why others were skipped."""
    columns = profile["columns"]
    rows = profile["row_count"]
    metrics = [c for c in columns if c["role_hint"] == "metric" and c["null_count"] < rows][:2]
    time = next((c for c in columns if c["role_hint"] == "time" and c["distinct_count"] >= 2), None)
    dims = [c for c in columns
            if c["role_hint"] == "dimension" and 2 <= c["distinct_count"] <= _MAX_GROUPS]
    count = {"name": "rows", "agg": "count"}
    used: set[str] = {"rows"}
    measures = [_measure(name, c["name"], used) for c in metrics]
    # Trend and breakdown need a figure that adds up across rows (a total, not a price).
    main = next((m for m in measures if m["agg"] == "sum"), count)

    plan, skipped = [], []
    plan.append(("totals", "Headline totals", _spec(name, [count, *measures])))
    if time:
        plan.append(("trend", f"{_label(main)} over time", _spec(
            name, [main],
            time_bucket={"column": {"dataset": name, "column": time["name"]},
                         "grain": _grain(time)},
            sort=[{"by": "time_bucket", "descending": False}])))
    else:
        skipped.append({"kind": "trend", "reason": "No date column with more than one value."})
    if dims:
        plan.append(("breakdown", f"{_label(main)} by {dims[0]['name']}", _spec(
            name, [main], dimensions=[{"dataset": name, "column": dims[0]["name"]}],
            sort=[{"by": main["name"], "descending": True}], limit=_TOP_N)))
    else:
        skipped.append({"kind": "breakdown",
                        "reason": f"No category column with 2 to {_MAX_GROUPS} values."})
    mix = dims[1] if len(dims) > 1 else (dims[0] if dims and main is not count else None)
    if mix:
        plan.append(("mix", f"Rows by {mix['name']}", _spec(
            name, [count], dimensions=[{"dataset": name, "column": mix["name"]}],
            sort=[{"by": "rows", "descending": True}], limit=_TOP_N)))
    else:
        skipped.append({"kind": "mix", "reason": "No second category column to count by."})
    return plan, skipped


def _spec(name: str, metrics: list[dict], **extra) -> AnalysisSpec:
    return AnalysisSpec.model_validate({"base_dataset": name, "metrics": metrics, **extra})


# Measures whose sum means nothing (a total of prices or percentages); they are averaged.
# A general reading of column-name words (whole tokens, so "monthly_fee" is still summed),
# not a list for any one dataset.
_NOT_ADDITIVE = {
    "price", "rate", "ratio", "pct", "percent", "percentage", "share", "score", "rating",
    "age", "avg", "average", "mean", "median", "temperature", "temp", "lat", "latitude",
    "lon", "lng", "longitude", "year", "month", "day", "hour", "index", "rank",
}


def _tokens(column: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", column)  # unitPrice -> unit Price
    return {t for t in re.split(r"[^a-z0-9]+", spaced.casefold()) if t}


def _measure(dataset: str, column: str, used: set[str]) -> dict:
    agg = "avg" if _tokens(column) & _NOT_ADDITIVE else "sum"
    return {"name": _metric_name(column, used, "average" if agg == "avg" else "total"),
            "agg": agg, "expr": {"kind": "col", "ref": {"dataset": dataset, "column": column}}}


def _metric_name(column: str, used: set[str], prefix: str) -> str:
    """A spec-safe output name derived from the column (spec names are ^[a-z][a-z0-9_]*$)."""
    base = re.sub(r"[^a-z0-9_]+", "_", column.casefold()).strip("_")[:50]
    base = base if base[:1].isalpha() else f"m_{base}".rstrip("_")
    candidate, n = f"{prefix}_{base}", 2
    while candidate in used:
        candidate, n = f"{prefix}_{base}_{n}", n + 1
    used.add(candidate)
    return candidate


def _label(metric: dict) -> str:
    return "Rows" if metric["agg"] == "count" else metric["name"].replace("_", " ").capitalize()


def _grain(column: dict) -> str:
    """Bucket size from the date span, so a 2-week log and a 5-year ledger both read well."""
    try:
        start = date.fromisoformat(str(column["min"])[:10])
        end = date.fromisoformat(str(column["max"])[:10])
    except (TypeError, ValueError):
        return "month"
    days = (end - start).days
    for limit, grain in ((45, "day"), (210, "week"), (1100, "month")):
        if days <= limit:
            return grain
    return "quarter"


def _overview_conversation(conn: sqlite3.Connection, user: User, dataset: sqlite3.Row) -> dict:
    for candidate in conn.execute(
        "SELECT id, state_json FROM conversations WHERE owner_id = ?", (user.id,)
    ):
        if json.loads(candidate["state_json"]).get("overview_of") == dataset["id"]:
            return store.get_conversation(conn, user, candidate["id"])
    created = store.create_conversation(
        conn, user, f"Overview · {dataset['name']}", [dataset["id"]]
    )
    state = {**created["state"], "overview_of": dataset["id"]}
    store.update_state(conn, user, created["id"], state)
    return {**created, "state": state}


def _card(conn: sqlite3.Connection, user: User, kind: str, result_id: str) -> dict:
    result = store.load_result(conn, user, result_id)
    return {
        "kind": kind,
        "result": result.model_dump(mode="json"),
        "status": store.result_status(conn, user, result),
    }


def _response(dataset, conversation_id, cards, skipped, reused) -> dict:
    return {
        "dataset_id": dataset["id"],
        "dataset_version": dataset["version"],
        "conversation_id": conversation_id,
        "reused": reused,
        "cards": cards,
        "skipped": skipped,
    }
