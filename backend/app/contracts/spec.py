"""AnalysisSpec: the only shape of analysis the planner may request.

The LLM fills this in; ``analysis.validator`` checks it against the schema and the
caller's permissions; ``analysis.compiler`` turns it into SQL. Nothing else reaches
the executor. See docs/PLAN.md section 3.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Scalar = str | int | float | bool


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColumnRef(_Strict):
    """A column of a dataset, addressed by the dataset's user-visible name."""

    dataset: str = Field(min_length=1, max_length=128)
    column: str = Field(min_length=1, max_length=256)


# ---- metric expressions: a tiny arithmetic AST, never a string -------------------


class Col(_Strict):
    kind: Literal["col"] = "col"
    ref: ColumnRef


class Num(_Strict):
    kind: Literal["num"] = "num"
    value: float


class BinOp(_Strict):
    kind: Literal["binop"] = "binop"
    op: Literal["+", "-", "*", "/"]
    left: Expr
    right: Expr


Expr = Annotated[Col | Num | BinOp, Field(discriminator="kind")]
BinOp.model_rebuild()


class Aggregate(StrEnum):
    sum = "sum"
    avg = "avg"
    count = "count"
    count_distinct = "count_distinct"
    min = "min"
    max = "max"
    median = "median"


class Metric(_Strict):
    """``name`` is the output column label. ``expr=None`` is only valid with ``count``
    (meaning COUNT(*))."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    agg: Aggregate
    expr: Expr | None = None


# ---- filters, grouping, joins --------------------------------------------------


class FilterOp(StrEnum):
    eq = "eq"
    ne = "ne"
    gt = "gt"
    gte = "gte"
    lt = "lt"
    lte = "lte"
    in_ = "in"
    not_in = "not_in"
    between = "between"
    is_null = "is_null"
    not_null = "not_null"


class Filter(_Strict):
    """``value`` is always bound as a SQL parameter, never interpolated.
    ``in``/``not_in`` take a list; ``between`` takes [low, high] inclusive;
    ``is_null``/``not_null`` take no value."""

    column: ColumnRef
    op: FilterOp
    value: Scalar | list[Scalar] | None = None


class TimeGrain(StrEnum):
    day = "day"
    week = "week"
    month = "month"
    quarter = "quarter"
    year = "year"


class TimeBucket(_Strict):
    column: ColumnRef
    grain: TimeGrain


class PreAggregate(_Strict):
    """Roll the many-side of a join up to its join key before joining, so the
    metric grain is preserved (PLAN.md join rule)."""

    metrics: list[Metric] = Field(min_length=1, max_length=3)


class Join(_Strict):
    """``left`` belongs to a dataset already in the query; ``right`` brings in a new
    one. Allowed only if ``right``'s key is unique (many-to-one / one-to-one), or
    if ``pre_aggregate`` is given. Checked on the data by ``analysis.joins``."""

    left: ColumnRef
    right: ColumnRef
    pre_aggregate: PreAggregate | None = None


class SortKey(_Strict):
    """``by`` is a metric name, a dimension column name, or ``"time_bucket"``."""

    by: str
    descending: bool = True


class AnalysisSpec(_Strict):
    base_dataset: str = Field(min_length=1, max_length=128)
    metrics: list[Metric] = Field(min_length=1, max_length=4)
    dimensions: list[ColumnRef] = Field(default_factory=list, max_length=2)
    time_bucket: TimeBucket | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=10)
    joins: list[Join] = Field(default_factory=list, max_length=2)
    sort: list[SortKey] = Field(default_factory=list, max_length=3)
    limit: int | None = Field(default=None, ge=1, le=1000)
