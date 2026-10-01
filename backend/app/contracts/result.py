"""AnalysisResult: the single source of truth for one answer.

Answer sentences, table, chart, SQL, dashboard cards and exports are all views of a
stored AnalysisResult. See docs/PLAN.md sections 1, 5, 6.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.outcomes import Outcome
from app.contracts.spec import AnalysisSpec


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- execution ------------------------------------------------------------------


class ColumnRole(StrEnum):
    dimension = "dimension"
    time = "time"
    metric = "metric"


class ResultColumn(_Model):
    name: str
    dtype: str  # duckdb type name, e.g. "DOUBLE", "VARCHAR", "DATE"
    role: ColumnRole


class ResultTable(_Model):
    columns: list[ResultColumn]
    rows: list[list[Any]]


class Caveat(_Model):
    """Produced from execution metadata, never by the model. Cannot be dropped by
    the composer. Codes: nulls_excluded, rows_truncated, type_coerced,
    join_orphans, derived_metric, partial_period, anomaly_values_excluded, ..."""

    code: str
    message: str
    count: int | None = None


class ExecutionMeta(_Model):
    sql: str
    params: list[Any]
    row_count: int
    truncated: bool
    elapsed_ms: int
    dataset_versions: dict[str, int]  # dataset_id -> version
    caveats: list[Caveat] = Field(default_factory=list)
    # Start date (ISO) of a final time bucket that holds data only up to part of its span.
    # Trend claims and anomaly detection skip it; its rows stay in the table.
    partial_period: str | None = None


# ---- charts ---------------------------------------------------------------------


class ChartType(StrEnum):
    bar = "bar"
    line = "line"
    area = "area"
    pie = "pie"
    scatter = "scatter"
    histogram = "histogram"


class ChartSpec(_Model):
    """Encoding only; the data is always the result_table of the same result."""

    type: ChartType
    x: str
    y: list[str] = Field(min_length=1, max_length=4)
    series: str | None = None
    title: str = Field(max_length=200)


# ---- claims ---------------------------------------------------------------------


class ClaimKind(StrEnum):
    value = "value"  # "<entity> had <metric> of <value> in <period>"
    rank = "rank"  # "<entity> had the highest/lowest <metric> (k of n)"
    comparison = "comparison"  # "<a> vs <b>: difference / ratio"
    share = "share"  # "<entity> accounted for x% of <metric>"
    trend = "trend"  # "<metric> rose/fell by x over <periods>"


class CellSource(_Model):
    type: Literal["cell"] = "cell"
    row_key: dict[str, Any]  # dimension/time column -> value identifying the row
    column: str  # metric column


class CalcSource(_Model):
    type: Literal["calc"] = "calc"
    op: Literal["difference", "ratio", "pct_change", "share_of_total"]
    inputs: list[str]  # claim ids


class Claim(_Model):
    id: str = Field(pattern=r"^c[0-9]{1,3}$")
    kind: ClaimKind
    metric: str
    entity: dict[str, Any] = Field(default_factory=dict)  # dimension -> value
    period: str | None = None
    value: float
    unit: str | None = None  # "GBP", "%", "orders", ...
    source: CellSource | CalcSource = Field(discriminator="type")
    # kind-specific evidence, checked by the validator
    rank: int | None = None  # 1 = highest (or lowest when direction == "lowest")
    rank_of: int | None = None
    direction: Literal["highest", "lowest", "up", "down", "flat"] | None = None
    compared_to: str | None = None  # claim id
    periods_covered: int | None = None  # trend claims need >= 3


# ---- anomalies ------------------------------------------------------------------


class AnomalyStatus(StrEnum):
    completed_none = "completed_none"
    completed_found = "completed_found"
    insufficient_data = "insufficient_data"
    execution_failed = "execution_failed"


class AnomalyFlag(_Model):
    method: Literal["robust_z"] = "robust_z"
    reference: str  # e.g. "own monthly history of region=West"
    row_key: dict[str, Any]
    metric: str
    value: float
    median: float
    mad: float
    robust_z: float
    threshold: float


class AnomalyReport(_Model):
    status: AnomalyStatus
    flags: list[AnomalyFlag] = Field(default_factory=list)
    reason: str | None = None  # required unless completed_*
    series_checked: int = 0
    series_skipped: int = 0
    excluded_values: int = 0


# ---- the result -----------------------------------------------------------------


class Clarification(_Model):
    question: str
    options: list[str] = Field(default_factory=list, max_length=6)


class AnalysisResult(_Model):
    id: str
    owner_id: str
    conversation_id: str
    question: str
    outcome: Outcome
    message: str | None = None  # human-readable reason for non-ok outcomes
    spec: AnalysisSpec | None = None
    table: ResultTable | None = None
    meta: ExecutionMeta | None = None
    chart: ChartSpec | None = None
    claims: list[Claim] = Field(default_factory=list)
    answer_sentences: list[str] = Field(default_factory=list)  # rendered by backend only
    anomalies: AnomalyReport | None = None
    clarification: Clarification | None = None
    suggested_questions: list[str] = Field(default_factory=list, max_length=5)
    trace: list[dict[str, Any]] = Field(default_factory=list)  # real tool-call trace
    created_at: datetime
