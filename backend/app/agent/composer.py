"""Render complete factual clauses from computed, validated claims."""

import math
from collections import defaultdict

from app.agent.validator import is_partial, metric_unit, validate_claims
from app.analysis.periods import period_label
from app.contracts.outcomes import Outcome
from app.contracts.result import (
    AnalysisResult,
    CalcSource,
    CellSource,
    Claim,
    ClaimKind,
    ColumnRole,
)
from app.contracts.spec import Aggregate


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
            if len(ordered) >= 2:  # a ranking (and a share) needs at least two groups
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
                if metric == metrics[0]:
                    claims.extend(_lead_context(result, claims[0], ordered, names, dimensions,
                                                len(claims)))
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
    sentences = _grouped_sentences(checked) or [_render(c, grain) for c in narrated]
    if result.anomalies:
        # The question was about unusual values, so they lead the answer.
        sentences = [*_anomaly_sentences(result, grain), *sentences[:MAX_CONTEXT_SENTENCES]]
    return result.model_copy(update={"claims": checked, "answer_sentences": sentences})


MAX_CONTEXT_SENTENCES = 3


def _lead_context(result, top, ordered, names, dimensions, next_id):
    """For the leading group: its share of the total and its gap to the runner-up, as claims the
    validator recomputes from the complete table (shares only where the measure adds up)."""
    metric = top.metric
    extra = []
    runner = sorted(ordered, key=lambda pair: pair[1], reverse=True)[1:2]
    if runner:
        row, value = runner[0]
        values = dict(zip(names, row, strict=True))
        second = Claim(
            id=f"c{next_id}", kind=ClaimKind.value, metric=metric,
            entity={n: values[n] for n in dimensions}, value=float(value),
            unit=metric_unit(result, metric),
            source=CellSource(row_key={n: values[n] for n in dimensions}, column=metric),
        )
        gap = top.value - second.value
        extra += [second, Claim(
            id=f"c{next_id + 1}", kind=ClaimKind.comparison, metric=metric, entity=top.entity,
            value=gap, unit=metric_unit(result, metric),
            direction="up" if gap > 0 else "down" if gap < 0 else "flat",
            compared_to=second.id, source=CalcSource(op="difference", inputs=[top.id, second.id]),
        )]
    agg = next((m.agg for m in (result.spec.metrics if result.spec else []) if m.name == metric),
               None)
    total = sum(v for _, v in ordered)
    # Shares only for measures that add up across groups; the validator enforces the same rule.
    if agg in (Aggregate.sum, Aggregate.count) and total > 0 and all(v >= 0 for _, v in ordered):
        extra.append(Claim(
            id=f"c{next_id + len(extra)}", kind=ClaimKind.share, metric=metric,
            entity=top.entity, value=top.value / total * 100, unit="%",
            source=CalcSource(op="share_of_total", inputs=[top.id]),
        ))
    return extra


def _grouped_sentences(checked):
    """One conclusion and one comparison for a ranked breakdown, instead of restating each
    group (the table already lists them). None when there is no validated ranking."""
    top = next((c for c in checked if c.kind == ClaimKind.rank), None)
    if top is None:
        return None
    subject = ", ".join(str(v) for v in top.entity.values())
    share = next((c for c in checked if c.kind == ClaimKind.share and c.entity == top.entity), None)
    value = _format(top.value, top.unit)
    if share:
        lead = (f"{subject} had the highest {_words(top.metric)}: {value} "
                f"({_format(share.value, '%')} of the total across {top.rank_of} groups).")
    else:
        lead = f"{subject} had the highest {_words(top.metric)} of {top.rank_of} groups: {value}."
    sentences = [lead]
    gap = next((c for c in checked if c.kind == ClaimKind.comparison
                and c.source.type == "calc" and c.source.op == "difference"), None)
    second = next((c for c in checked if gap and c.id == gap.compared_to), None)
    if gap and second:
        runner = ", ".join(str(v) for v in second.entity.values())
        sentences.append(
            f"That is {_format(abs(gap.value), gap.unit)} more than {runner} "
            f"({_format(second.value, second.unit)})."
        )
    others = [c for c in checked if c.kind == ClaimKind.rank and c is not top]
    return sentences + [_render(c) for c in others]
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
        when = ", ".join(period_label(v, grain) for k, v in flag.row_key.items() if k in times)
        label = ", ".join(x for x in (entity, when) if x) or "One observation"
        side = "high" if flag.value > flag.median else "low"
        typical = f"{entity}'s typical" if entity else "the typical"
        sentences.append(
            f"{label}: {_words(flag.metric)} was {_format(flag.value, unit)}, unusually {side} "
            f"against {typical} {_words(flag.metric)} of {_format(flag.median, unit)} "
            f"(robust z {flag.robust_z:,.2f}; threshold {flag.threshold:,.2g})."
        )
    return sentences


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
        period = " to ".join(period_label(p, grain) for p in claim.period.split(" to "))
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
