"""Render complete factual clauses from computed, validated claims."""

import math
from collections import defaultdict

from app.agent.validator import is_partial, metric_unit, validate_claims
from app.contracts.outcomes import Outcome
from app.contracts.result import (
    AnalysisResult,
    CalcSource,
    CellSource,
    Claim,
    ClaimKind,
    ColumnRole,
)


def compose(result: AnalysisResult) -> AnalysisResult:
    if result.table is None:
        return result.model_copy(update={"claims": [], "answer_sentences": []})
    names = [c.name for c in result.table.columns]
    dimensions = [c.name for c in result.table.columns if c.role == ColumnRole.dimension]
    times = [c.name for c in result.table.columns if c.role == ColumnRole.time]
    metrics = [c.name for c in result.table.columns if c.role == ColumnRole.metric]
    claims = []
    # Select a readable subset; the complete table remains available.
    for row in result.table.rows[:6]:
        values = dict(zip(names, row, strict=True))
        for metric in metrics:
            value = values[metric]
            if not _number(value):
                continue
            claims.append(
                Claim(
                    id=f"c{len(claims)}",
                    kind=ClaimKind.value,
                    metric=metric,
                    entity={n: values[n] for n in dimensions},
                    period=str(values[times[0]]) if times else None,
                    value=float(value),
                    unit=metric_unit(result, metric),
                    source=CellSource(
                        row_key={n: values[n] for n in [*dimensions, *times]}, column=metric
                    ),
                )
            )
    complete = not (result.meta and result.meta.truncated or result.spec and result.spec.limit)
    if complete and not times and dimensions:
        for metric in metrics:
            ordered = [
                (r, r[names.index(metric)])
                for r in result.table.rows
                if _number(r[names.index(metric)])
            ]
            if ordered:
                row, value = max(ordered, key=lambda pair: pair[1])
                values = dict(zip(names, row, strict=True))
                claims.insert(
                    0,
                    Claim(
                        id=f"c{len(claims)}",
                        kind=ClaimKind.rank,
                        metric=metric,
                        entity={n: values[n] for n in dimensions},
                        value=float(value),
                        unit=metric_unit(result, metric),
                        rank=1,
                        rank_of=len(ordered),
                        direction="highest",
                        source=CellSource(
                            row_key={n: values[n] for n in dimensions}, column=metric
                        ),
                    ),
                )
    if complete and times:
        for metric in metrics[:1]:
            grouped = defaultdict(list)
            for row in result.table.rows:
                period = row[names.index(times[0])]
                if (
                    _number(row[names.index(metric)])
                    and period is not None
                    and not is_partial(result, period)
                ):
                    grouped[tuple(row[names.index(d)] for d in dimensions)].append(row)
            for entity, rows in list(grouped.items())[:3]:
                rows.sort(key=lambda r: str(r[names.index(times[0])]))
                if len(rows) >= 3:
                    change = rows[-1][names.index(metric)] - rows[0][names.index(metric)]
                    last = dict(zip(names, rows[-1], strict=True))
                    claims.insert(
                        0,
                        Claim(
                            id=f"c{len(claims)}",
                            kind=ClaimKind.trend,
                            metric=metric,
                            entity=dict(zip(dimensions, entity, strict=True)),
                            value=float(change),
                            unit=metric_unit(result, metric),
                            period=(
                                f"{rows[0][names.index(times[0])]} to "
                                f"{rows[-1][names.index(times[0])]}"
                            ),
                            direction="up" if change > 0 else "down" if change < 0 else "flat",
                            periods_covered=len(rows),
                            source=CellSource(
                                row_key={n: last[n] for n in [*dimensions, *times]}, column=metric
                            ),
                        ),
                    )
                if len(rows) >= 2 and rows[-2][names.index(metric)] != 0:
                    pair = []
                    for row in rows[-2:]:
                        values = dict(zip(names, row, strict=True))
                        period = str(values[times[0]])
                        existing = next(
                            (
                                c
                                for c in claims
                                if c.kind == ClaimKind.value
                                and c.metric == metric
                                and c.period == period
                                and c.entity == dict(zip(dimensions, entity, strict=True))
                            ),
                            None,
                        )
                        if existing is None:
                            existing = Claim(
                                id=f"c{len(claims)}",
                                kind=ClaimKind.value,
                                metric=metric,
                                entity=dict(zip(dimensions, entity, strict=True)),
                                period=period,
                                value=float(values[metric]),
                                unit=metric_unit(result, metric),
                                source=CellSource(
                                    row_key={n: values[n] for n in [*dimensions, *times]},
                                    column=metric,
                                ),
                            )
                            claims.append(existing)
                        pair.append(existing)
                    previous, current = pair
                    pct = (current.value - previous.value) / abs(previous.value) * 100
                    claims.append(
                        Claim(
                            id=f"c{len(claims)}",
                            kind=ClaimKind.comparison,
                            metric=metric,
                            entity=current.entity,
                            period=current.period,
                            value=pct,
                            unit="%",
                            direction="up" if pct > 0 else "down" if pct < 0 else "flat",
                            periods_covered=2,
                            compared_to=previous.id,
                            source=CalcSource(op="pct_change", inputs=[current.id, previous.id]),
                        )
                    )
    candidate = result.model_copy(update={"claims": claims})
    checked = validate_claims(candidate)
    if len(checked) != len(claims):
        return result.model_copy(
            update={
                "claims": [],
                "answer_sentences": [],
                "outcome": Outcome.narrative_unverified,
                "message": (
                    "The written summary could not be validated. The calculated table is available."
                ),
            }
        )
    sentences = [_render(c) for c in checked]
    if result.anomalies:
        report = result.anomalies
        sentences.append(
            f"Anomaly check: {report.status.value.replace('_', ' ')}; "
            f"{len(report.flags)} values flagged across {report.series_checked} series."
        )
        for flag in report.flags[:3]:
            label = ", ".join(str(v) for v in flag.row_key.values()) or "Observation"
            unit = metric_unit(result, flag.metric)
            sentences.append(
                f"{label}: {flag.metric} was {_format(flag.value, unit, '.6g')}, compared with "
                f"a median of {_format(flag.median, unit, '.6g')} in {flag.reference}. Its "
                f"robust z-score {flag.robust_z:,.6g} exceeded {flag.threshold:,.6g}."
            )
    return result.model_copy(update={"claims": checked, "answer_sentences": sentences})


def _format(value, unit, general=".12g"):
    """Number text for a unit: GBP as £1,234.50, counts as integers, % with a sign-free tail."""
    if unit == "GBP":
        return f"{'-' if value < 0 else ''}£{abs(value):,.2f}"
    if unit == "count":
        return f"{round(value):,}"
    if unit == "%":
        return f"{value:,.2f}".rstrip("0").rstrip(".") + "%"
    return f"{value:,{general}}"


def _render(claim):
    subject = ", ".join(str(v) for v in claim.entity.values()) or "The selected data"
    if claim.kind == ClaimKind.rank:
        return (
            f"{subject} had the highest {claim.metric}: {_format(claim.value, claim.unit)} "
            f"among {claim.rank_of} returned groups."
        )
    if claim.kind == ClaimKind.trend:
        word = {"up": "increased", "down": "decreased", "flat": "changed"}[claim.direction]
        return (
            f"{subject}: {claim.metric} {word} by {_format(abs(claim.value), claim.unit)} "
            f"from {claim.period}, across {claim.periods_covered} periods."
        )
    if claim.kind == ClaimKind.comparison:
        word = {"up": "increased", "down": "decreased", "flat": "changed"}[claim.direction]
        return (
            f"{subject}: {claim.metric} {word} by {_format(abs(claim.value), claim.unit)} "
            f"in {claim.period} versus the previous observed period."
        )
    period = f" in {claim.period}" if claim.period else ""
    return f"{subject}: {claim.metric}{period} was {_format(claim.value, claim.unit)}."


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
