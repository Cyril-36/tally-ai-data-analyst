"""Authorized analytical tools and context-aware execution caching."""

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

from app.analysis.anomalies import detect
from app.analysis.catalog import Catalog, DatasetInfo, lookup_dataset
from app.analysis.compiler import dataset_names
from app.analysis.executor import execute, run_spec
from app.analysis.joins import check_join
from app.analysis.validator import validate
from app.auth import authz
from app.auth.authz import Action
from app.auth.deps import User
from app.charts.builder import default_chart, validate_chart
from app.config import get_settings
from app.contracts.outcomes import AppError, Outcome
from app.contracts.result import (
    AnalysisResult,
    AnomalyReport,
    AnomalyStatus,
    Caveat,
    ChartSpec,
    ColumnRole,
    ExecutionMeta,
    ResultTable,
)
from app.contracts.spec import AnalysisSpec, Join
from app.datasets.quality import quality_report
from app.db import dataset_parquet_path

log = logging.getLogger("app.analysis")


def build_catalog(conn: sqlite3.Connection, user: User, dataset_ids: list[str]) -> Catalog:
    catalog: Catalog = {}
    for dataset_id in dict.fromkeys(dataset_ids):
        row = authz.require_owned(conn, "datasets", dataset_id, user, Action.analyze)
        profile = json.loads(row["profile_json"])
        path = dataset_parquet_path(row["id"], row["version"])
        if not path.exists():
            raise AppError(
                Outcome.execution_failed, "Stored dataset data is missing. Upload it again."
            )
        catalog[row["name"]] = DatasetInfo(
            row["id"],
            row["name"],
            row["version"],
            path,
            {c["name"]: c["dtype"] for c in profile["columns"]},
        )
    return catalog


def schema_context(conn: sqlite3.Connection, user: User, catalog: Catalog) -> list[dict]:
    context = []
    for info in catalog.values():
        row = authz.require_owned(conn, "datasets", info.id, user, Action.read)
        profile = json.loads(row["profile_json"])
        context.append(
            {
                "name": info.name,
                "row_count": profile["row_count"],
                "version": info.version,
                "columns": [
                    {
                        "name": c["name"],
                        "dtype": c["dtype"],
                        "role_hint": c["role_hint"],
                        "sample_values": [str(v)[:80] for v in c["sample_values"][:3]],
                    }
                    for c in profile["columns"]
                ],
            }
        )
    return context


@lru_cache
def executor_version() -> str:
    analysis = Path(__file__).resolve().parents[1] / "analysis"
    return hashlib.sha256(
        b"".join(
            (analysis / f).read_bytes()
            for f in ["compiler.py", "validator.py", "worker.py", "executor.py"]
        )
    ).hexdigest()


def cache_key(spec: AnalysisSpec, catalog: Catalog, user: User) -> str:
    payload = {
        "owner": user.id,
        "spec": spec.model_dump(mode="json"),
        "datasets": {catalog[n].id: catalog[n].version for n in dataset_names(spec)},
        "executor": executor_version(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def assert_versions(conn: sqlite3.Connection, user: User, versions: dict[str, int]) -> None:
    for dataset_id, version in versions.items():
        row = authz.require_owned(conn, "datasets", dataset_id, user, Action.analyze)
        if row["version"] != version:
            raise AppError(Outcome.invalid_input, "Dataset changed during analysis. Please retry.")


def analyze(
    conn: sqlite3.Connection,
    user: User,
    conversation_id: str,
    question: str,
    spec: AnalysisSpec,
    dataset_ids: list[str],
) -> AnalysisResult:
    # Always rebuild the authorized catalog, including on cache hits.
    catalog = build_catalog(conn, user, dataset_ids)
    validate(spec, catalog)
    key = cache_key(spec, catalog, user)
    entry = conn.execute(
        "SELECT result_json FROM cache WHERE key = ? AND owner_id = ?", (key, user.id)
    ).fetchone()
    if entry:
        payload = json.loads(entry["result_json"])
        table = ResultTable.model_validate(payload["table"])
        meta = ExecutionMeta.model_validate(payload["meta"])
        assert_versions(conn, user, meta.dataset_versions)
    else:
        table, meta = run_spec(spec, catalog)
        assert_versions(conn, user, meta.dataset_versions)
        # Include ingestion warnings without letting a model omit them.
        for name in dataset_names(spec):
            row = authz.require_owned(conn, "datasets", catalog[name].id, user, Action.read)
            for warning in json.loads(row["profile_json"]).get("warnings", []):
                meta.caveats.append(Caveat(code="ingestion_warning", message=warning))
        conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)",
            (
                key,
                user.id,
                json.dumps(
                    {"table": table.model_dump(mode="json"), "meta": meta.model_dump(mode="json")}
                ),
                datetime.now(UTC).isoformat(),
            ),
        )
    # Declared currencies are read fresh on every call, so a cached table never carries a stale one.
    meta.currencies, meta.source_currencies = {}, {}
    for name in dataset_names(spec):
        row = authz.require_owned(conn, "datasets", catalog[name].id, user, Action.read)
        currency = json.loads(row["profile_json"]).get("currency")
        meta.source_currencies[row["id"]] = currency
        if currency:
            meta.currencies[name] = currency
    result = AnalysisResult(
        id=uuid.uuid4().hex,
        owner_id=user.id,
        conversation_id=conversation_id,
        question=question,
        outcome=Outcome.ok,
        spec=spec,
        table=table,
        meta=meta,
        created_at=datetime.now(UTC),
        chart=default_chart(table),
    )
    result.trace.append(
        {
            "tool": "run_analysis",
            "cache_hit": bool(entry),
            "row_count": meta.row_count,
            "result_id": result.id,
        }
    )
    if not table.rows:
        result.message = "No rows matched the selected filters."
    log.info(
        "analysis",
        extra={
            "user_id": user.id,
            "cache_hit": bool(entry),
            "elapsed_ms": meta.elapsed_ms,
            "result_id": result.id,
        },
    )
    return result


def detect_result(result: AnalysisResult, metric: str) -> None:
    if result.table is None:
        raise AppError(Outcome.invalid_input, "There is no result table to check.")
    columns = result.table.columns
    if metric not in {c.name for c in columns if c.role == ColumnRole.metric}:
        raise AppError(Outcome.invalid_input, "Select a metric in this result.")
    if result.meta and result.meta.truncated or result.spec and result.spec.limit is not None:
        result.anomalies = AnomalyReport(
            status=AnomalyStatus.insufficient_data,
            reason="Anomaly checks require an uncapped population.",
        )
        return
    time_col = next((c.name for c in columns if c.role == ColumnRole.time), None)
    groups = [c.name for c in columns if c.role == ColumnRole.dimension] if time_col else []
    partial = result.meta.partial_period if result.meta else None
    result.anomalies = detect(result.table, metric, groups, time_col, partial)
    if result.meta:
        if result.anomalies.excluded_values:
            result.meta.caveats.append(
                Caveat(
                    code="anomaly_values_excluded",
                    count=result.anomalies.excluded_values,
                    message=(
                        f"{result.anomalies.excluded_values} values (missing, or in the "
                        "incomplete final period) were excluded from anomaly detection."
                    ),
                )
            )
        if result.anomalies.series_skipped:
            result.meta.caveats.append(
                Caveat(
                    code="anomaly_series_skipped",
                    count=result.anomalies.series_skipped,
                    message=result.anomalies.reason or "Some series could not be checked.",
                )
            )


def inspect(conn: sqlite3.Connection, user: User, catalog: Catalog, name: str) -> dict:
    info = lookup_dataset(catalog, name)
    row = authz.require_owned(conn, "datasets", info.id, user, Action.read)
    return json.loads(row["profile_json"])


def quality(conn: sqlite3.Connection, user: User, catalog: Catalog, name: str) -> dict:
    info = lookup_dataset(catalog, name)
    profile = inspect(conn, user, catalog, name)
    configured = get_settings().demo_reference_date
    return quality_report(
        info.parquet_path, profile, date.fromisoformat(configured) if configured else date.today()
    )


def relationship(catalog: Catalog, join: Join) -> dict:
    # Lookup first: the executor only gets this user's selected datasets.
    lookup_dataset(catalog, join.left.dataset)
    lookup_dataset(catalog, join.right.dataset)
    report = check_join(join, catalog, execute)
    return vars(report)


def set_chart(result: AnalysisResult, chart: ChartSpec) -> None:
    if result.table is None:
        raise AppError(Outcome.invalid_input, "There is no result table to chart.")
    try:
        result.chart = validate_chart(result.table, chart)
    except ValueError as exc:
        raise AppError(Outcome.invalid_input, str(exc)) from None
