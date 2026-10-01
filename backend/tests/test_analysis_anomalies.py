import math

import pandas as pd
import pytest

from app.analysis.anomalies import MIN_POINTS, THRESHOLD, detect
from app.contracts.outcomes import AppError, Outcome
from app.contracts.result import AnomalyStatus, ColumnRole, ResultColumn, ResultTable

# Plausible monthly values around 100: median 100, nothing near the 3.5 cut-off.
NORMAL = [100, 102, 98, 101, 99, 103, 97, 100, 101, 99, 102, 98]


def months(n: int) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.date_range("2024-01-01", periods=n, freq="MS")]


def series_table(groups: dict[str, list]) -> ResultTable:
    """(period, region, revenue) rows: one monthly series per region."""
    rows = [
        [months(len(values))[i], region, value]
        for region, values in groups.items()
        for i, value in enumerate(values)
    ]
    return ResultTable(
        columns=[
            ResultColumn(name="period", dtype="DATE", role=ColumnRole.time),
            ResultColumn(name="region", dtype="VARCHAR", role=ColumnRole.dimension),
            ResultColumn(name="revenue", dtype="DOUBLE", role=ColumnRole.metric),
        ],
        rows=rows,
    )


def run(groups, group_cols=("region",), time_col="period"):
    return detect(series_table(groups), "revenue", list(group_cols), time_col)


def test_planted_spike_is_flagged_with_its_evidence():
    values = [*NORMAL]
    values[7] = 1000
    report = run({"West": values})
    assert report.status == AnomalyStatus.completed_found
    assert (report.series_checked, report.series_skipped) == (1, 0)
    [flag] = report.flags
    assert flag.row_key == {"period": "2024-08-01", "region": "West"}
    assert flag.metric == "revenue" and flag.method == "robust_z"
    assert flag.value == 1000
    assert flag.median == 100.5
    assert flag.mad == 1.5
    assert flag.threshold == THRESHOLD == 3.5
    assert flag.robust_z == pytest.approx(abs(1000 - 100.5) / (1.4826 * 1.5))
    assert flag.robust_z > THRESHOLD
    assert "region=West" in flag.reference and "own history" in flag.reference


def test_a_drop_is_flagged_too_and_flags_are_sorted_by_strength():
    values = [*NORMAL]
    values[2] = 20
    values[9] = 400
    report = run({"West": values})
    assert [f.value for f in report.flags] == [400, 20]  # strongest first
    assert report.flags[0].robust_z > report.flags[1].robust_z
    assert all(f.robust_z > 0 for f in report.flags)  # absolute distance


def test_clean_data_completes_with_no_flags():
    report = run({"West": NORMAL, "East": [v * 5 for v in NORMAL]})
    assert report.status == AnomalyStatus.completed_none
    assert report.flags == []
    assert (report.series_checked, report.series_skipped) == (2, 0)
    assert report.reason is None


def test_constant_series_is_insufficient_not_clean():
    report = run({"West": [50.0] * 12})
    assert report.status == AnomalyStatus.insufficient_data
    assert report.flags == []
    assert "constant" in report.reason
    assert (report.series_checked, report.series_skipped) == (0, 1)


def test_mostly_identical_series_has_zero_mad_and_is_skipped():
    report = run({"West": [5] * 11 + [500]})
    assert report.status == AnomalyStatus.insufficient_data
    assert "zero median absolute deviation" in report.reason


def test_fewer_than_eight_points_is_insufficient():
    report = run({"West": NORMAL[: MIN_POINTS - 1]})
    assert report.status == AnomalyStatus.insufficient_data
    assert "fewer than 8" in report.reason
    assert report.series_skipped == 1


def test_exactly_eight_points_is_enough():
    report = run({"West": NORMAL[:MIN_POINTS]})
    assert report.status == AnomalyStatus.completed_none
    assert report.series_checked == 1


def test_missing_values_are_excluded_and_counted():
    values = [*NORMAL, None, math.nan, math.inf]
    report = run({"West": values})
    assert report.status == AnomalyStatus.completed_none
    assert "3 missing values excluded" in report.reason


def test_missing_values_can_push_a_series_below_the_minimum():
    values = [*NORMAL[:7], None, math.nan]
    report = run({"West": values})
    assert report.status == AnomalyStatus.insufficient_data
    assert "2 missing values excluded" in report.reason
    assert "fewer than 8" in report.reason


def test_a_missing_value_is_never_treated_as_zero():
    values = [*NORMAL[:10], None, None]
    report = run({"West": values})
    assert report.status == AnomalyStatus.completed_none  # zeros would have been flagged


def test_groups_are_never_pooled():
    small = [9, 10, 11, 10, 9, 11, 10, 10, 9, 11, 10, 10]  # ~10
    large = [990, 1000, 1010, 1000, 990, 1010, 1000, 1000]  # ~1000
    groups = {"Small": small, "Large": large}

    per_group = run(groups)
    assert per_group.status == AnomalyStatus.completed_none
    assert per_group.series_checked == 2

    # Control: pooled, the large group's normal level looks like an outlier.
    pooled = run(groups, group_cols=())
    assert pooled.status == AnomalyStatus.completed_found
    assert {f.row_key["region"] for f in pooled.flags} == {"Large"}


def test_only_the_group_with_the_spike_is_flagged():
    spiky = [*NORMAL]
    spiky[4] = 900
    report = run({"West": NORMAL, "East": spiky, "North": [v * 10 for v in NORMAL]})
    assert report.status == AnomalyStatus.completed_found
    assert [f.row_key["region"] for f in report.flags] == ["East"]
    assert "region=East" in report.flags[0].reference
    assert report.series_checked == 3


def test_partial_coverage_is_reported_in_the_reason():
    report = run({"West": NORMAL, "East": [7.0] * 12, "North": NORMAL[:4]})
    assert report.status == AnomalyStatus.completed_none
    assert (report.series_checked, report.series_skipped) == (1, 2)
    assert "2 of 3 series skipped" in report.reason


def test_cross_section_compares_peers_within_the_period():
    products = {f"P{i}": v for i, v in enumerate([10, 12, 11, 9, 10, 11, 12, 10, 11, 200])}
    table = ResultTable(
        columns=[
            ResultColumn(name="product", dtype="VARCHAR", role=ColumnRole.dimension),
            ResultColumn(name="revenue", dtype="DOUBLE", role=ColumnRole.metric),
        ],
        rows=[[p, v] for p, v in products.items()],
    )
    report = detect(table, "revenue", [], None)
    assert report.status == AnomalyStatus.completed_found
    [flag] = report.flags
    assert flag.row_key == {"product": "P9"}
    assert flag.reference == "all peers (10 rows)"


def test_cross_section_peers_are_per_group():
    # Within each category the peers are similar; across them the scales differ.
    rows = [["cheap", f"c{i}", 10 + (i % 3)] for i in range(10)]
    rows += [["dear", f"d{i}", 5000 + 10 * (i % 3)] for i in range(10)]
    table = ResultTable(
        columns=[
            ResultColumn(name="category", dtype="VARCHAR", role=ColumnRole.dimension),
            ResultColumn(name="product", dtype="VARCHAR", role=ColumnRole.dimension),
            ResultColumn(name="revenue", dtype="DOUBLE", role=ColumnRole.metric),
        ],
        rows=rows,
    )
    assert detect(table, "revenue", ["category"], None).status == AnomalyStatus.completed_none


def test_empty_result_is_insufficient():
    report = run({})
    assert report.status == AnomalyStatus.insufficient_data
    assert "no rows" in report.reason


def test_unknown_columns_are_rejected_with_the_valid_ones():
    table = series_table({"West": NORMAL})
    for kwargs in (
        dict(metric="profit", group_cols=[], time_col=None),
        dict(metric="revenue", group_cols=["country"], time_col=None),
        dict(metric="revenue", group_cols=[], time_col="month"),
    ):
        with pytest.raises(AppError) as err:
            detect(table, **kwargs)
        assert err.value.outcome == Outcome.invalid_input
        assert "period, region, revenue" in err.value.message


def test_other_metric_columns_are_not_part_of_the_row_key():
    table = ResultTable(
        columns=[
            ResultColumn(name="period", dtype="DATE", role=ColumnRole.time),
            ResultColumn(name="revenue", dtype="DOUBLE", role=ColumnRole.metric),
            ResultColumn(name="orders", dtype="BIGINT", role=ColumnRole.metric),
        ],
        rows=[[months(12)[i], v, 1] for i, v in enumerate([*NORMAL[:11], 5000])],
    )
    [flag] = detect(table, "revenue", [], "period").flags
    assert flag.row_key == {"period": "2024-12-01"}
