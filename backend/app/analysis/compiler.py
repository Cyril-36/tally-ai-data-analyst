"""Compile a validated AnalysisSpec into DuckDB SQL with sqlglot.

Identifiers are always quoted and every filter value is a ``?`` parameter, so nothing a user
or the model typed ever becomes SQL text. Dataset ``i`` of the query becomes table ``t{i}``
(``t0`` is the base dataset); the executor loads one table per entry of ``tables``.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlglot import exp

from app.analysis.catalog import Catalog
from app.config import get_settings
from app.contracts.result import ColumnRole
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

DIALECT = "duckdb"


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    params: list[Any]
    tables: dict[str, Path]  # view name -> parquet path
    result_roles: dict[str, ColumnRole]
    limit: int  # rows to keep; the SQL fetches limit + 1 to detect truncation
    capped: bool  # True when `limit` came from the row cap, not from the spec
    null_check_sql: str | None  # same params as `sql`; one count column per input below
    null_check_inputs: list[str]  # "dataset.column" labels, in null_check_sql column order


def dataset_names(spec: AnalysisSpec) -> list[str]:
    return [spec.base_dataset] + [j.right.dataset for j in spec.joins]


def compile_spec(spec: AnalysisSpec, catalog: Catalog) -> CompiledQuery:
    names = dataset_names(spec)
    tables = {f"t{i}": catalog[n].parquet_path for i, n in enumerate(names)}
    table_of = {n: f"t{i}" for i, n in enumerate(names)}
    # Column refs resolve to the table, or to the pre-aggregate subquery alias for that dataset.
    alias_of = dict(table_of)
    for join in spec.joins:
        if join.pre_aggregate:
            alias_of[join.right.dataset] = "p" + table_of[join.right.dataset][1:]

    group, select, roles = [], [], {}
    if spec.time_bucket:
        period = _period(spec.time_bucket.column, spec.time_bucket.grain.value, alias_of, catalog)
        group.append(period)
        select.append(exp.alias_(period.copy(), "period", quoted=True))
        roles["period"] = ColumnRole.time
    for ref in spec.dimensions:
        col = _col(alias_of[ref.dataset], ref.column)
        group.append(col)
        select.append(exp.alias_(col.copy(), ref.column, quoted=True))
        roles[ref.column] = ColumnRole.dimension
    for metric in spec.metrics:
        select.append(exp.alias_(_aggregate(metric, alias_of), metric.name, quoted=True))
        roles[metric.name] = ColumnRole.metric

    conditions, params = [], []
    for f in spec.filters:
        conditions.append(_condition(f, alias_of, catalog, params))

    def frame(query: exp.Select) -> exp.Select:
        query = query.from_(_table(table_of[spec.base_dataset]))
        for join in spec.joins:
            query = query.join(
                _join_target(join, table_of, alias_of),
                on=exp.EQ(
                    this=_col(alias_of[join.left.dataset], join.left.column),
                    expression=_col(alias_of[join.right.dataset], join.right.column),
                ),
                join_type="left",
            )
        if conditions:
            query = query.where(exp.and_(*[c.copy() for c in conditions], wrap=False), copy=False)
        return query

    query = frame(exp.select(*select))
    if group:
        query = query.group_by(*[g.copy() for g in group], copy=False)
        query = query.order_by(*_order(spec, roles), copy=False)

    cap = get_settings().result_row_cap
    limit = min(spec.limit or cap, cap)
    query = query.limit(limit + 1, copy=False)

    inputs = _null_check_inputs(spec, alias_of)
    null_sql = None
    if inputs:
        counts = [
            exp.alias_(
                exp.Filter(
                    this=exp.Count(this=exp.Star()),
                    expression=exp.Where(
                        this=exp.Is(this=_col(alias_of[ds], c), expression=exp.Null())
                    ),
                ),
                f"n{i}",
                quoted=True,
            )
            for i, (ds, c) in enumerate(inputs)
        ]
        null_sql = frame(exp.select(*counts)).sql(dialect=DIALECT, identify=True)

    return CompiledQuery(
        sql=query.sql(dialect=DIALECT, identify=True),
        params=params,
        tables=tables,
        result_roles=roles,
        limit=limit,
        capped=spec.limit is None or spec.limit > cap,
        null_check_sql=null_sql,
        null_check_inputs=[f"{ds}.{c}" for ds, c in inputs],
    )


# ---- building blocks ------------------------------------------------------------


def _ident(name: str) -> exp.Identifier:
    return exp.to_identifier(name, quoted=True)


def _col(table: str, column: str) -> exp.Column:
    return exp.column(column, table=table, quoted=True)


def _table(name: str) -> exp.Table:
    return exp.Table(this=_ident(name))


def _period(
    ref: ColumnRef, grain: str, alias_of: dict[str, str], catalog: Catalog
) -> exp.Expression:
    trunc = exp.Anonymous(
        this="date_trunc",
        expressions=[exp.Literal.string(grain), _col(alias_of[ref.dataset], ref.column)],
    )
    # DuckDB's date_trunc returns a TIMESTAMP even for a DATE input; keep dates as dates.
    if catalog[ref.dataset].columns[ref.column].upper() == "DATE":
        return exp.Cast(this=trunc, to=exp.DataType.build("DATE"))
    return trunc


def _expression(expr: Expr, alias_of: dict[str, str]) -> exp.Expression:
    if isinstance(expr, Col):
        return _col(alias_of[expr.ref.dataset], expr.ref.column)
    if isinstance(expr, Num):
        value = expr.value
        return exp.Literal.number(str(int(value)) if value.is_integer() else repr(value))

    def operand(e: Expr) -> exp.Expression:
        inner = _expression(e, alias_of)
        return exp.Paren(this=inner) if isinstance(e, BinOp) else inner

    left, right = operand(expr.left), operand(expr.right)
    if expr.op == "+":
        return exp.Add(this=left, expression=right)
    if expr.op == "-":
        return exp.Sub(this=left, expression=right)
    if expr.op == "*":
        return exp.Mul(this=left, expression=right)
    divisor = exp.Nullif(this=_expression(expr.right, alias_of), expression=exp.Literal.number(0))
    return exp.Div(this=left, expression=divisor)


def _aggregate(metric: Metric, alias_of: dict[str, str]) -> exp.Expression:
    if metric.expr is None:
        return exp.Count(this=exp.Star())
    x = _expression(metric.expr, alias_of)
    match metric.agg:
        case Aggregate.sum:
            return exp.Sum(this=x)
        case Aggregate.avg:
            return exp.Avg(this=x)
        case Aggregate.min:
            return exp.Min(this=x)
        case Aggregate.max:
            return exp.Max(this=x)
        case Aggregate.median:
            return exp.Median(this=x)
        case Aggregate.count_distinct:
            return exp.Count(this=exp.Distinct(expressions=[x]))
        case _:
            return exp.Count(this=x)


def _join_target(join: Join, table_of: dict[str, str], alias_of: dict[str, str]) -> exp.Expression:
    table = table_of[join.right.dataset]
    if join.pre_aggregate is None:
        return _table(table)
    # Roll the many side up to the join key first so the metric grain is preserved.
    key = join.right.column
    inner_alias = {join.right.dataset: table}
    rolled = exp.select(
        exp.alias_(_col(table, key), key, quoted=True),
        *[
            exp.alias_(_aggregate(m, inner_alias), m.name, quoted=True)
            for m in join.pre_aggregate.metrics
        ],
    ).from_(_table(table))
    rolled = rolled.group_by(_col(table, key), copy=False)
    return exp.Subquery(
        this=rolled, alias=exp.TableAlias(this=_ident(alias_of[join.right.dataset]))
    )


def _condition(
    f: Filter, alias_of: dict[str, str], catalog: Catalog, params: list[Any]
) -> exp.Expression:
    column = _col(alias_of[f.column.dataset], f.column.column)
    dtype = catalog[f.column.dataset].columns.get(f.column.column, "")
    cast_to = _temporal_cast(dtype)

    def bind(value: Any) -> exp.Expression:
        params.append(value)
        placeholder = exp.Placeholder()
        return (
            exp.Cast(this=placeholder, to=exp.DataType.build(cast_to)) if cast_to else placeholder
        )

    match f.op:
        case FilterOp.is_null:
            return exp.Is(this=column, expression=exp.Null())
        case FilterOp.not_null:
            return exp.Not(this=exp.Is(this=column, expression=exp.Null()))
        case FilterOp.in_ | FilterOp.not_in:
            test = exp.In(this=column, expressions=[bind(v) for v in f.value])
            return test if f.op == FilterOp.in_ else exp.Not(this=test)
        case FilterOp.between:
            low, high = f.value
            return exp.Between(this=column, low=bind(low), high=bind(high))
    cls = {
        FilterOp.eq: exp.EQ,
        FilterOp.ne: exp.NEQ,
        FilterOp.gt: exp.GT,
        FilterOp.gte: exp.GTE,
        FilterOp.lt: exp.LT,
        FilterOp.lte: exp.LTE,
    }[f.op]
    return cls(this=column, expression=bind(f.value))


def _temporal_cast(dtype: str) -> str:
    d = dtype.upper()
    if d == "DATE":
        return "DATE"
    if d.startswith("TIMESTAMP"):
        return "TIMESTAMPTZ" if "TIME ZONE" in d or d == "TIMESTAMPTZ" else "TIMESTAMP"
    return ""


def _order(spec: AnalysisSpec, roles: dict[str, ColumnRole]) -> list[exp.Ordered]:
    if spec.sort:
        keys = [("period" if k.by == "time_bucket" else k.by, k.descending) for k in spec.sort]
    elif spec.time_bucket:
        keys = [("period", False)]
    else:
        keys = [(spec.metrics[0].name, True)]
    # Ties are broken by the grouping columns so repeated runs return identical rows.
    chosen = {name for name, _ in keys}
    keys += [(n, False) for n, r in roles.items() if r != ColumnRole.metric and n not in chosen]
    return [exp.Ordered(this=_ident(name), desc=desc) for name, desc in keys]


def _null_check_inputs(spec: AnalysisSpec, alias_of: dict[str, str]) -> list[tuple[str, str]]:
    """Raw columns feeding a metric (pre-aggregated ones are skipped), de-duplicated."""
    seen: dict[tuple[str, str], None] = {}

    def walk(e: Expr) -> None:
        if isinstance(e, Col):
            if alias_of[e.ref.dataset].startswith("t"):
                seen[(e.ref.dataset, e.ref.column)] = None
        elif isinstance(e, BinOp):
            walk(e.left)
            walk(e.right)

    for m in spec.metrics:
        if m.expr is not None:
            walk(m.expr)
    return list(seen)
