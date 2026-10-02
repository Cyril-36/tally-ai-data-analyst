"""Render complete factual clauses from computed, validated claims."""

import math
from collections import defaultdict
from datetime import date

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
    complete = not (result.meta and result.meta.truncated or result.spec and result.spec.limit)
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
    grain = result.spec.time_bucket.grain if result.spec and result.spec.time_bucket else None
    # A series with a trend or latest change is summarised by those; its individual period
    # values (first rows, comparison inputs) stay as claims but are not narrated.
    summarised = [c.entity for c in checked if c.kind in (ClaimKind.trend, ClaimKind.comparison)]
    narrated = [
        c for c in checked if not (times and c.kind == ClaimKind.value and c.entity in summarised)
    ]
    sentences = [_render(c, grain) for c in narrated]
    if result.anomalies:
        # The question was about unusual values, so they lead the answer.
        sentences = [*_anomaly_sentences(result, grain), *sentences[:MAX_CONTEXT_SENTENCES]]
    return result.model_copy(update={"claims": checked, "answer_sentences": sentences})


MAX_CONTEXT_SENTENCES = 3
_CURRENCY_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


def _anomaly_sentences(result, grain):
    report = result.anomalies
    if report.status.value == "completed_none":
        return [f"No unusual values found across {report.series_checked} series."]
    if report.status.value != "completed_found":
        return [f"Unusual values could not be checked: {report.reason or 'not enough data'}."]
    count = len(report.flags)
    plural = "value" if count == 1 else "values"
    sentences = [f"{count} unusual {plural} found across {report.series_checked} series."]
    for flag in report.flags[:5]:
        unit = metric_unit(result, flag.metric)
        times = {c.name for c in result.table.columns if c.role == ColumnRole.time}
        entity = ", ".join(str(v) for k, v in flag.row_key.items() if k not in times)
        when = ", ".join(_period(v, grain) for k, v in flag.row_key.items() if k in times)
        label = ", ".join(x for x in (entity, when) if x) or "One observation"
        side = "high" if flag.value > flag.median else "low"
        typical = f"{entity}'s typical" if entity else "the typical"
        sentences.append(
            f"{label}: {_words(flag.metric)} was {_format(flag.value, unit)}, unusually {side} "
            f"against {typical} {_words(flag.metric)} of {_format(flag.median, unit)} "
            f"(robust z {flag.robust_z:,.2f}; threshold {flag.threshold:,.2g})."
        )
    return sentences


def _period(value, grain):
    """ISO period start as people read it: Jan 2026, Q1 2026, 2026, 3 Feb 2026."""
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    if grain == "month":
        return day.strftime("%b %Y")
    if grain == "quarter":
        return f"Q{(day.month - 1) // 3 + 1} {day.year}"
    if grain == "year":
        return str(day.year)
    if grain == "week":
        return f"week of {day.day} {day.strftime('%b %Y')}"
    return f"{day.day} {day.strftime('%b %Y')}"


def _format(value, unit, general=".12g"):
    """Number text for a unit: a declared currency as £1,234.50 / $… / €… (other codes as
    "CHF 1,234.50"), counts as integers, % with a sign-free tail."""
    if unit in _CURRENCY_SYMBOLS or (unit and len(unit) == 3 and unit.isupper()):
        symbol = _CURRENCY_SYMBOLS.get(unit, f"{unit} ")
        return f"{'-' if value < 0 else ''}{symbol}{abs(value):,.2f}"
    if unit == "count":
        return f"{round(value):,}"
    if unit == "%":
        return f"{value:,.2f}".rstrip("0").rstrip(".") + "%"
    return f"{value:,{general}}"


def _render(claim, grain=None):
    subject = ", ".join(str(v) for v in claim.entity.values()) or "The selected data"
    if claim.period and grain:
        period = " to ".join(_period(p, grain) for p in claim.period.split(" to "))
        claim = claim.model_copy(update={"period": period})  # display only; validated already
    if claim.kind == ClaimKind.rank:
        return (
            f"{subject} had the highest {_words(claim.metric)}: {_format(claim.value, claim.unit)} "
            f"among {claim.rank_of} returned groups."
        )
    if claim.kind == ClaimKind.trend:
        word = {"up": "increased", "down": "decreased", "flat": "changed"}[claim.direction]
        return (
            f"{subject}: {_words(claim.metric)} {word} by {_format(abs(claim.value), claim.unit)} "
            f"from {claim.period}, across {claim.periods_covered} periods."
        )
    if claim.kind == ClaimKind.comparison:
        word = {"up": "increased", "down": "decreased", "flat": "changed"}[claim.direction]
        return (
            f"{subject}: {_words(claim.metric)} {word} by {_format(abs(claim.value), claim.unit)} "
            f"in {claim.period} versus the previous observed period."
        )
    period = f" in {claim.period}" if claim.period else ""
    if claim.unit == "count":
        return f"{subject}{period}: {_format(claim.value, claim.unit)} {_words(claim.metric)}."
    return f"{subject}: {_words(claim.metric)}{period} was {_format(claim.value, claim.unit)}."


def _words(metric):
    """Output names read as words in sentences: total_quantity -> total quantity."""
    return metric.replace("_", " ")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
