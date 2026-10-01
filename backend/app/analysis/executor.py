"""Run compiled SQL in an isolated worker process (PLAN.md section 3, Tier A).

Each call starts a fresh ``worker.py`` with an empty environment (PATH and TZ only), an empty
temp dir as cwd and private copies of the Parquet files. The parent enforces the wall-clock
timeout with SIGKILL and always deletes the temp dir. ``run_spec`` is the one entry point that
turns an AnalysisSpec into a ResultTable plus execution metadata.
"""

import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.analysis.catalog import Catalog
from app.analysis.compiler import compile_spec, dataset_names
from app.analysis.joins import check_join
from app.analysis.validator import expr_text, validate
from app.config import get_settings
from app.contracts.outcomes import AppError, Outcome
from app.contracts.result import Caveat, ExecutionMeta, ResultColumn, ResultTable
from app.contracts.spec import AnalysisSpec, BinOp, Metric

WORKER = Path(__file__).with_name("worker.py")
_VIEW = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")


@dataclass
class QueryResult:
    columns: list[tuple[str, str]]  # (name, duckdb type)
    rows: list[list[Any]]


@dataclass
class ExecOutput:
    main: QueryResult
    extra: list[QueryResult]  # results of extra_queries, in order
    meta: dict[str, Any]  # elapsed_ms, query_ms, limits (applied rlimits), env_keys, cwd


def execute(
    sql: str,
    params: list[Any],
    tables: dict[str, Path],
    extra_queries: list[tuple[str, list[Any]]] | None = None,
) -> ExecOutput:
    settings = get_settings()
    queries = [(sql, params), *(extra_queries or [])]
    workdir = Path(tempfile.mkdtemp(prefix="analysis-"))
    try:
        files = {}
        for view, source in tables.items():
            if not _VIEW.match(view):
                raise ValueError(f"invalid table name {view!r}")
            files[view] = f"{view}.parquet"
            shutil.copyfile(source, workdir / files[view])
        request = {
            "queries": [{"sql": s, "params": p} for s, p in queries],
            "tables": files,
            "memory_mb": settings.worker_memory_mb,
            "limits": {
                "RLIMIT_AS": (settings.worker_memory_mb * 2 + 1024) * 1024 * 1024,
                "RLIMIT_CPU": math.ceil(settings.query_timeout_s * 2) + 1,
                "RLIMIT_NOFILE": 64,
                "RLIMIT_FSIZE": 0,
            },
        }
        started = time.perf_counter()
        response = _run_worker(request, workdir, settings.query_timeout_s)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if not response.get("ok"):
        raise AppError(
            Outcome.execution_failed, f"Query failed: {_short(response.get('error', ''))}"
        )
    results = [
        QueryResult([(n, t) for n, t in r["columns"]], r["rows"]) for r in response["results"]
    ]
    meta = {**response["meta"], "elapsed_ms": elapsed_ms}
    return ExecOutput(main=results[0], extra=results[1:], meta=meta)


def _run_worker(request: dict, workdir: Path, timeout_s: float) -> dict:
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no user input
        [sys.executable, "-I", str(WORKER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TZ": "UTC"},
        cwd=workdir,
        text=True,
        start_new_session=True,  # own process group so the kill below reaches every thread/child
    )
    try:
        stdout, _ = proc.communicate(json.dumps(request), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise AppError(Outcome.execution_failed, "Query exceeded the time limit") from None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        # Killed by a resource limit, or crashed: nothing trustworthy came back.
        raise AppError(
            Outcome.execution_failed, "Query failed: it was stopped for using too many resources"
        ) from None


def _short(message: str) -> str:
    """First line only, with file paths removed (they could reveal server layout)."""
    lines = message.strip().splitlines()
    first = lines[0] if lines else "unknown error"
    return re.sub(r"(?<![\w])(?:/[\w.\-]+)+", "<path>", first)[:300]


# ---- spec -> result ---------------------------------------------------------------


def run_spec(spec: AnalysisSpec, catalog: Catalog) -> tuple[ResultTable, ExecutionMeta]:
    validate(spec, catalog)
    caveats: list[Caveat] = []
    for join in spec.joins:
        check = check_join(join, catalog, execute)
        if not check.allowed:
            raise AppError(Outcome.unsupported, check.reason or "Join refused")
        if check.orphan_left_rows:
            caveats.append(
                Caveat(
                    code="join_orphans",
                    message=(
                        f"{check.orphan_left_rows} {join.left.dataset} rows have no matching "
                        f"{join.right.dataset} row; their {join.right.dataset} values are empty."
                    ),
                    count=check.orphan_left_rows,
                )
            )

    compiled = compile_spec(spec, catalog)
    extra = [(compiled.null_check_sql, compiled.params)] if compiled.null_check_sql else []
    out = execute(compiled.sql, compiled.params, compiled.tables, extra)

    rows = out.main.rows[: compiled.limit]
    truncated = compiled.capped and len(out.main.rows) > compiled.limit
    if truncated:
        caveats.append(
            Caveat(
                code="rows_truncated",
                message=f"The result was cut at {compiled.limit} rows; more rows matched.",
                count=compiled.limit,
            )
        )
    if extra:
        for label, count in zip(compiled.null_check_inputs, out.extra[0].rows[0], strict=True):
            if count:
                caveats.append(
                    Caveat(
                        code="nulls_excluded",
                        message=f"{count} rows had an empty {label} and were left out of the "
                        "calculation.",
                        count=count,
                    )
                )
    for metric in _all_metrics(spec):
        if isinstance(metric.expr, BinOp):
            text = f"{metric.agg.value}({expr_text(metric.expr)})"
            note = " Rows where a divisor is zero are left out." if " / " in text else ""
            caveats.append(
                Caveat(
                    code="derived_metric",
                    message=f"'{metric.name}' is calculated as {text}.{note}",
                )
            )

    table = ResultTable(
        columns=[
            ResultColumn(name=name, dtype=dtype, role=compiled.result_roles[name])
            for name, dtype in out.main.columns
        ],
        rows=rows,
    )
    meta = ExecutionMeta(
        sql=compiled.sql,
        params=compiled.params,
        row_count=len(rows),
        truncated=truncated,
        elapsed_ms=out.meta["elapsed_ms"],
        dataset_versions={catalog[n].id: catalog[n].version for n in dataset_names(spec)},
        caveats=caveats,
    )
    return table, meta


def _all_metrics(spec: AnalysisSpec) -> list[Metric]:
    pre = [m for j in spec.joins if j.pre_aggregate for m in j.pre_aggregate.metrics]
    return [*spec.metrics, *pre]
