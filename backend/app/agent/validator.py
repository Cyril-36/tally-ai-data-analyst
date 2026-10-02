"""Validate factual semantics and values; templates, not model prose, render them."""

import math
import re

from app.contracts.result import CalcSource, ClaimKind, ColumnRole
from app.contracts.spec import Aggregate, BinOp, Col

# Money-like column names. Deliberately excludes generic words such as "value" or "score".
_MONEY = re.compile(r"price|amount|revenue|cost|sales|profit|discount|spend|fee|income", re.I)


def metric_unit(result, metric):
    """Unit of a result metric, derived from the spec and data (never chosen by the model):
    "count" for counting aggregates; a currency code only when the metric reads as money AND
    every dataset its columns come from declares that same currency; otherwise None."""
    specs = result.spec.metrics if result.spec else []
    found = next((m for m in specs if m.name == metric), None)
    if found is None:
        return None
    if found.agg in (Aggregate.count, Aggregate.count_distinct):
        return "count"
    refs = _refs(found.expr)
    if not refs or not any(_MONEY.search(n) for n in [metric, *(r.column for r in refs)]):
        return None
    declared = result.meta.currencies if result.meta else {}
    currencies = {declared.get(r.dataset) for r in refs}
    return currencies.pop() if len(currencies) == 1 and None not in currencies else None


def _refs(expr):
    if isinstance(expr, Col):
        return [expr.ref]
    if isinstance(expr, BinOp):
        return [*_refs(expr.left), *_refs(expr.right)]
    return []


def is_partial(result, period):
    """True for the incomplete final time bucket (ExecutionMeta.partial_period)."""
    partial = result.meta.partial_period if result.meta else None
    return partial is not None and period is not None and str(period)[:10] == partial


def validate_claims(result):
    if result.table is None:
        return []
    columns = result.table.columns
    names = [c.name for c in columns]
    roles = {c.name: c.role for c in columns}
    dimensions = [c.name for c in columns if c.role == ColumnRole.dimension]
    times = [c.name for c in columns if c.role == ColumnRole.time]
    keys = [*dimensions, *times]
    rows = [dict(zip(names, row, strict=True)) for row in result.table.rows]
    accepted = []
    known = {}
    for claim in result.claims:
        if isinstance(claim.source, CalcSource):
            expected_unit = {
                "pct_change": "%",
                "share_of_total": "%",
                "difference": metric_unit(result, claim.metric),
            }.get(claim.source.op)
        else:
            expected_unit = metric_unit(result, claim.metric)
        if claim.id in known or claim.unit != expected_unit or not math.isfinite(claim.value):
            continue
        if isinstance(claim.source, CalcSource):
            if not _calc(claim, known, result, rows, times):
                continue
        else:
            source = claim.source
            if roles.get(source.column) != ColumnRole.metric or claim.metric != source.column:
                continue
            if set(source.row_key) != set(keys):
                continue
            matches = [r for r in rows if all(r[k] == v for k, v in source.row_key.items())]
            if len(matches) != 1:
                continue
            row = matches[0]
            entity = {k: row[k] for k in dimensions}
            if entity != claim.entity:
                continue
            if claim.kind == ClaimKind.trend:
                if not times or not _complete(result):
                    continue
                series = [
                    r
                    for r in rows
                    if all(r[k] == v for k, v in entity.items())
                    and r[times[0]] is not None
                    and not is_partial(result, r[times[0]])
                    and _number(r[claim.metric])
                ]
                series.sort(key=lambda r: str(r[times[0]]))
                if len(series) < 3:
                    continue
                change = series[-1][claim.metric] - series[0][claim.metric]
                direction = "up" if change > 0 else "down" if change < 0 else "flat"
                if (
                    claim.periods_covered != len(series)
                    or claim.direction != direction
                    or claim.period != f"{series[0][times[0]]} to {series[-1][times[0]]}"
                    or not _same(claim.value, change)
                ):
                    continue
            else:
                period = str(row[times[0]]) if times else None
                if claim.period != period or not _same(claim.value, row[claim.metric]):
                    continue
                if claim.kind == ClaimKind.rank:
                    if not _complete(result) or claim.direction not in {"highest", "lowest"}:
                        continue
                    population = [r[claim.metric] for r in rows if _number(r[claim.metric])]
                    rank = 1 + sum(
                        v > claim.value if claim.direction == "highest" else v < claim.value
                        for v in population
                    )
                    if claim.rank != rank or claim.rank_of != len(population):
                        continue
                elif claim.kind != ClaimKind.value or any(
                    x is not None
                    for x in (
                        claim.rank,
                        claim.rank_of,
                        claim.direction,
                        claim.periods_covered,
                        claim.compared_to,
                    )
                ):
                    continue
        accepted.append(claim)
        known[claim.id] = claim
    return accepted


def _complete(result):
    return not (result.meta and result.meta.truncated or result.spec and result.spec.limit)


def _calc(claim, known, result, rows, times):
    inputs = [known.get(i) for i in claim.source.inputs]
    if claim.source.op == "share_of_total":
        return _share(claim, inputs, result, rows, times)
    if len(inputs) != 2 or any(c is None for c in inputs):
        return False
    a, b = inputs
    if (
        a.metric != b.metric
        or claim.metric != a.metric
        or claim.entity != a.entity
        or claim.compared_to != b.id
    ):
        return False
    if claim.source.op == "difference":
        if claim.period != a.period or a.period != b.period:
            return False
        expected = a.value - b.value
    elif b.value == 0:
        return False
    elif claim.source.op == "ratio":
        if claim.period != a.period or a.period != b.period:
            return False
        expected = a.value / b.value
    elif claim.source.op == "pct_change":
        if not times or not _complete(result) or a.period == b.period:
            return False
        series = [
            r
            for r in rows
            if all(r[k] == v for k, v in a.entity.items())
            and r[times[0]] is not None
            and not is_partial(result, r[times[0]])
            and _number(r[a.metric])
        ]
        series.sort(key=lambda r: str(r[times[0]]))
        if (
            len(series) < 2
            or claim.period != a.period
            or str(series[-1][times[0]]) != a.period
            or str(series[-2][times[0]]) != b.period
            or claim.periods_covered != 2
        ):
            return False
        expected = (a.value - b.value) / abs(b.value) * 100
    else:
        # A model-selected denominator is not proof of a population total.
        return False
    direction = "up" if expected > 0 else "down" if expected < 0 else "flat"
    return (
        claim.kind == ClaimKind.comparison
        and _same(claim.value, expected)
        and claim.direction == direction
    )


def _share(claim, inputs, result, rows, times):
    """A share is checked against the total recomputed from the complete table, and only for
    measures that add up across groups (a sum or a row count; not averages or distinct counts,
    whose groups can overlap)."""
    if len(inputs) != 1 or inputs[0] is None or times or not _complete(result):
        return False
    (part,) = inputs
    spec_metric = next((m for m in (result.spec.metrics if result.spec else [])
                        if m.name == claim.metric), None)
    if spec_metric is None or spec_metric.agg not in (Aggregate.sum, Aggregate.count):
        return False
    values = [r[claim.metric] for r in rows if _number(r[claim.metric])]
    total = sum(values)
    if (
        claim.kind != ClaimKind.share
        or part.metric != claim.metric
        or part.entity != claim.entity
        or total <= 0
        or any(v < 0 for v in values)
    ):
        return False
    return _same(claim.value, part.value / total * 100)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _same(a, b):
    return _number(b) and math.isclose(a, float(b), rel_tol=1e-9, abs_tol=1e-9)
