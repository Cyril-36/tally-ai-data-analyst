"""Check an AnalysisSpec against the caller's catalog before anything is compiled.

Every message is written so an LLM can repair the spec from it: it names the offending
item and lists what is valid. Unknown and not-authorized datasets look the same because the
catalog only ever contains authorized ones.
"""

import math
import re
from datetime import date

from app.analysis.catalog import (
    Catalog,
    describe_columns,
    is_bool,
    is_numeric,
    is_temporal,
    lookup_dataset,
    unknown_column_error,
)
from app.contracts.outcomes import AppError, Outcome
from app.contracts.spec import (
    Aggregate,
    AnalysisSpec,
    BinOp,
    Col,
    ColumnRef,
    Expr,
    Filter,
    FilterOp,
    Join,
    Metric,
    Num,
)

_NUMERIC_AGGS = {Aggregate.sum, Aggregate.avg, Aggregate.median, Aggregate.min, Aggregate.max}
_ORDERING_OPS = {FilterOp.gt, FilterOp.gte, FilterOp.lt, FilterOp.lte, FilterOp.between}
_MAX_IN_VALUES = 100
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")

# dataset name -> columns usable in this query (column name -> dtype)
Scope = dict[str, dict[str, str]]


def invalid(message: str) -> AppError:
    return AppError(Outcome.invalid_input, message)


def validate(spec: AnalysisSpec, catalog: Catalog) -> None:
    base = lookup_dataset(catalog, spec.base_dataset)
    scope: Scope = {base.name: dict(base.columns)}
    for join in spec.joins:
        _check_join(join, scope, catalog)

    for metric in spec.metrics:
        _check_metric(metric, scope)

    dim_names = [ref.column for ref in spec.dimensions]
    for ref in spec.dimensions:
        _resolve(ref, scope)
    if spec.time_bucket:
        _check_time_bucket(spec.time_bucket.column, scope)
    for f in spec.filters:
        _check_filter(f, scope)

    output_names = list(dim_names)
    if spec.time_bucket:
        output_names.append("period")
    output_names += [m.name for m in spec.metrics]
    duplicates = sorted({n for n in output_names if output_names.count(n) > 1})
    if duplicates:
        raise invalid(
            f"Output column names must be unique but {', '.join(duplicates)} appear more than "
            "once (metric names cannot equal a dimension column name or 'period'). "
            "Rename the metric."
        )

    sortable = set(dim_names) | {m.name for m in spec.metrics}
    if spec.time_bucket:
        if "time_bucket" in sortable:
            raise invalid("'time_bucket' is reserved for time sorting when a time bucket is used. "
                          "Rename the metric or use a different dimension.")
        sortable.add("time_bucket")
    for key in spec.sort:
        if key.by not in sortable:
            raise invalid(
                f"Cannot sort by '{key.by}'. Sort keys must be a metric name, a dimension "
                f"column name or 'time_bucket'. Valid sort keys: {', '.join(sorted(sortable))}."
            )


# ---- joins ----------------------------------------------------------------------


def _check_join(join: Join, scope: Scope, catalog: Catalog) -> None:
    left_ds = join.left.dataset
    if left_ds not in scope:
        raise invalid(
            f"Join left side '{left_ds}.{join.left.column}' is not in the query yet. "
            f"Datasets already in the query: {', '.join(scope)}. "
            "The left side of a join must belong to one of them."
        )
    left_type = _resolve(join.left, scope)

    right = lookup_dataset(catalog, join.right.dataset)
    if right.name in scope:
        raise invalid(f"Dataset '{right.name}' is already in the query and cannot be joined again.")
    if join.right.column not in right.columns:
        raise unknown_column_error(right.name, join.right.column, right.columns)
    right_type = right.columns[join.right.column]
    if _family(left_type) != _family(right_type):
        raise invalid(
            f"Join keys have incompatible types: {left_ds}.{join.left.column} is {left_type} "
            f"but {right.name}.{join.right.column} is {right_type}."
        )

    if join.pre_aggregate is None:
        scope[right.name] = dict(right.columns)
        return

    names = [m.name for m in join.pre_aggregate.metrics]
    if len(set(names)) != len(names) or join.right.column in names:
        raise invalid(
            f"Pre-aggregate metric names for '{right.name}' must be unique and different from "
            f"the join key '{join.right.column}'."
        )
    inner: Scope = {right.name: dict(right.columns)}
    for metric in join.pre_aggregate.metrics:
        _check_metric(metric, inner)
    # After pre-aggregation only the rolled-up metrics exist on the right side.
    scope[right.name] = {name: "DOUBLE" for name in names}


# ---- metrics --------------------------------------------------------------------


def _check_metric(metric: Metric, scope: Scope) -> None:
    if metric.expr is None:
        if metric.agg != Aggregate.count:
            raise invalid(
                f"Metric '{metric.name}': {metric.agg.value} needs an expression; only count may "
                "omit it (it then counts rows)."
            )
        return
    expr_type = _expr_type(metric.expr, scope)
    if metric.agg in _NUMERIC_AGGS and not is_numeric(expr_type):
        raise invalid(
            f"Metric '{metric.name}': {metric.agg.value} needs a numeric expression but "
            f"{expr_text(metric.expr)} is {expr_type}. Use count or count_distinct for "
            "non-numeric columns."
        )


def _expr_type(expr: Expr, scope: Scope) -> str:
    if isinstance(expr, Col):
        return _resolve(expr.ref, scope)
    if isinstance(expr, Num):
        if not math.isfinite(expr.value):
            raise invalid("Numbers in metric expressions must be finite.")
        return "DOUBLE"
    assert isinstance(expr, BinOp)
    for side in (expr.left, expr.right):
        side_type = _expr_type(side, scope)
        if not is_numeric(side_type):
            raise invalid(
                f"Arithmetic '{expr.op}' needs numeric operands but {expr_text(side)} "
                f"is {side_type}."
            )
    return "DOUBLE"


def expr_text(expr: Expr) -> str:
    if isinstance(expr, Col):
        return f"{expr.ref.dataset}.{expr.ref.column}"
    if isinstance(expr, Num):
        return f"{expr.value:g}"
    return f"({expr_text(expr.left)} {expr.op} {expr_text(expr.right)})"


# ---- columns, time buckets, filters ----------------------------------------------


def _resolve(ref: ColumnRef, scope: Scope) -> str:
    """Return the dtype of ``ref`` or raise with the valid alternatives."""
    if ref.dataset not in scope:
        raise invalid(
            f"Dataset '{ref.dataset}' is not part of this query. Datasets in the query: "
            f"{', '.join(scope)}. Add a join to bring another dataset in."
        )
    columns = scope[ref.dataset]
    if ref.column not in columns:
        raise unknown_column_error(ref.dataset, ref.column, columns)
    return columns[ref.column]


def _check_time_bucket(ref: ColumnRef, scope: Scope) -> None:
    dtype = _resolve(ref, scope)
    if not is_temporal(dtype):
        temporal = {
            f"{ds}.{c}": t for ds, cols in scope.items() for c, t in cols.items() if is_temporal(t)
        }
        raise invalid(
            f"Time bucket needs a date or timestamp column but {ref.dataset}.{ref.column} is "
            f"{dtype}. Date/timestamp columns available: {describe_columns(temporal)}."
        )


def _family(dtype: str) -> str:
    if is_numeric(dtype):
        return "numeric"
    if is_temporal(dtype):
        return "temporal"
    if is_bool(dtype):
        return "boolean"
    return "text"


def _check_filter(f: Filter, scope: Scope) -> None:
    dtype = _resolve(f.column, scope)
    where = f"Filter on {f.column.dataset}.{f.column.column} ({dtype})"
    op, value = f.op, f.value

    if op in (FilterOp.is_null, FilterOp.not_null):
        if value is not None:
            raise invalid(f"{where}: {op.value} takes no value.")
        return
    if op in _ORDERING_OPS and is_bool(dtype):
        raise invalid(f"{where}: {op.value} is not valid for boolean columns; use eq or ne.")

    if op in (FilterOp.in_, FilterOp.not_in, FilterOp.between):
        if not isinstance(value, list):
            raise invalid(f"{where}: {op.value} needs a list of values.")
        if op == FilterOp.between and len(value) != 2:
            raise invalid(f"{where}: between needs exactly two values [low, high].")
        if op != FilterOp.between and not 1 <= len(value) <= _MAX_IN_VALUES:
            raise invalid(f"{where}: {op.value} needs between 1 and {_MAX_IN_VALUES} values.")
        values = value
    else:
        if value is None or isinstance(value, list):
            raise invalid(f"{where}: {op.value} needs a single value.")
        values = [value]

    for v in values:
        if not _value_matches(v, dtype):
            raise invalid(f"{where}: value {v!r} does not match the column type. {_hint(dtype)}")


def _value_matches(v: object, dtype: str) -> bool:
    family = _family(dtype)
    if family == "numeric":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if family == "boolean":
        return isinstance(v, bool)
    if family == "temporal":
        if not isinstance(v, str):
            return False
        pattern = _DATE_RE if dtype.upper() == "DATE" else _TIMESTAMP_RE
        if not pattern.match(v):
            return False
        try:
            date.fromisoformat(v[:10])
        except ValueError:
            return False
        return True
    return isinstance(v, str)


def _hint(dtype: str) -> str:
    family = _family(dtype)
    if family == "numeric":
        return "Use a JSON number."
    if family == "boolean":
        return "Use true or false."
    if family == "temporal":
        return "Use an ISO date string 'YYYY-MM-DD'" + (
            "." if dtype.upper() == "DATE" else " or 'YYYY-MM-DD HH:MM:SS'."
        )
    return "Use a string."
