"""Robust z-score anomaly detection (PLAN.md section 9): one method, applied only where valid.

A point is flagged when ``|x - median| / (1.4826 * MAD) > 3.5``, where the median and MAD come
from an explicit reference population:

* with ``time_col``: each group (the distinct values of ``group_cols``) is compared with its
  own history, so a region is never judged against another region's scale;
* without ``time_col``: each group's rows are peers of each other (all rows are one group when
  ``group_cols`` is empty). Callers filter a cross-section to a single period first.

A series needs at least 8 non-null points and a MAD above zero, otherwise it is skipped.
Missing values are excluded and counted in ``reason``. ``mad`` in a flag is the raw MAD.
"""

import math
from statistics import median
from typing import Any

from app.contracts.outcomes import AppError, Outcome
from app.contracts.result import (
    AnomalyFlag,
    AnomalyReport,
    AnomalyStatus,
    ColumnRole,
    ResultTable,
)

THRESHOLD = 3.5
MIN_POINTS = 8
_MAD_TO_SIGMA = 1.4826


def detect(
    table: ResultTable, metric: str, group_cols: list[str], time_col: str | None
) -> AnomalyReport:
    names = [c.name for c in table.columns]
    for name in [metric, *group_cols, *([time_col] if time_col else [])]:
        if name not in names:
            raise AppError(
                Outcome.invalid_input,
                f"Unknown column '{name}' in the result. Valid columns: {', '.join(names)}.",
            )
    pos = {n: i for i, n in enumerate(names)}
    key_cols = [c.name for c in table.columns if c.role != ColumnRole.metric]

    # group value tuple -> [(row_key, value)]
    series: dict[tuple, list[tuple[dict[str, Any], float]]] = {}
    excluded = 0
    for row in table.rows:
        points = series.setdefault(tuple(row[pos[g]] for g in group_cols), [])
        value = _number(row[pos[metric]])
        if value is None:
            excluded += 1
        else:
            points.append(({k: row[pos[k]] for k in key_cols}, value))

    flags: list[AnomalyFlag] = []
    too_few = constant = zero_mad = checked = 0
    for group, points in series.items():
        if len(points) < MIN_POINTS:
            too_few += 1
            continue
        values = [v for _, v in points]
        mid = median(values)
        mad = median(abs(v - mid) for v in values)
        if mad == 0:
            if min(values) == max(values):
                constant += 1
            else:
                zero_mad += 1
            continue
        checked += 1
        reference = _reference(group_cols, group, time_col, metric, len(points))
        for row_key, value in points:
            z = abs(value - mid) / (_MAD_TO_SIGMA * mad)
            if z > THRESHOLD:
                flags.append(
                    AnomalyFlag(
                        reference=reference,
                        row_key=row_key,
                        metric=metric,
                        value=value,
                        median=mid,
                        mad=mad,
                        robust_z=z,
                        threshold=THRESHOLD,
                    )
                )
    flags.sort(key=lambda f: f.robust_z, reverse=True)

    skipped = too_few + constant + zero_mad
    notes = []
    if skipped:
        parts = [
            f"{n} {what}"
            for n, what in (
                (too_few, f"with fewer than {MIN_POINTS} non-null points"),
                (constant, "constant (no variation)"),
                (zero_mad, "with zero median absolute deviation"),
            )
            if n
        ]
        notes.append(f"{skipped} of {len(series)} series skipped: {', '.join(parts)}")
    if excluded:
        notes.append(f"{excluded} missing values excluded")

    if checked == 0:
        status = AnomalyStatus.insufficient_data
        notes.insert(0, "No series could be checked" if series else "The result has no rows")
    else:
        status = AnomalyStatus.completed_found if flags else AnomalyStatus.completed_none
    return AnomalyReport(
        status=status,
        flags=flags,
        reason="; ".join(notes) or None,
        series_checked=checked,
        series_skipped=skipped,
        excluded_values=excluded,
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _reference(
    group_cols: list[str], group: tuple, time_col: str | None, metric: str, n: int
) -> str:
    label = ", ".join(f"{c}={v}" for c, v in zip(group_cols, group, strict=True))
    if time_col:
        return f"own history of {label or metric} ({n} points over {time_col})"
    return f"peers within {label} ({n} rows)" if label else f"all peers ({n} rows)"
