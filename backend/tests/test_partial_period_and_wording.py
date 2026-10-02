"""Partial final period (excluded from trends and anomalies) and unit-aware sentence wording."""

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from app.agent.composer import compose
from app.agent.tools import detect_result
from app.agent.validator import validate_claims
from app.analysis.anomalies import detect
from app.analysis.executor import _bucket_bounds, run_spec
from app.contracts.outcomes import Outcome
from app.contracts.result import (
    AnalysisResult,
    ColumnRole,
    ExecutionMeta,
    ResultColumn,
    ResultTable,
)
from tests.test_analysis_data import make_catalog, ref, revenue_expr, sample_frames, spec

MONTHLY_REVENUE = dict(
    base_dataset="orders",
    metrics=[{"name": "revenue", "agg": "sum", "expr": revenue_expr()}],
    time_bucket={"column": ref("orders", "order_date"), "grain": "month"},
)


def orders_frame(dates: list[date], amounts: list[float]) -> dict[str, pd.DataFrame]:
    frames = sample_frames()
    n = len(dates)
    frames["orders"] = pd.DataFrame(
        {
            "order_id": range(n),
            "customer_id": [10] * n,
            "product_id": [1] * n,
            "region": ["West"] * n,
            "order_date": dates,
            "quantity": [1] * n,
            "unit_price": amounts,
            "discount_amount": [0.0] * n,
            "status": ["paid"] * n,
        }
    )
    return frames


def months(first_year: int, first_month: int, count: int) -> list[date]:
    return [
        date(first_year + (first_month - 1 + i) // 12, (first_month - 1 + i) % 12 + 1, 15)
        for i in range(count)
    ]


def answer(catalog, query: dict) -> AnalysisResult:
    s = spec(**query)
    table, meta = run_spec(s, catalog)
    return AnalysisResult(
        id="r1",
        owner_id="u1",
        conversation_id="c1",
        question="q",
        outcome=Outcome.ok,
        spec=s,
        table=table,
        meta=meta,
        created_at=datetime.now(UTC),
    )


# ---- partial final period -----------------------------------------------------


def test_partial_last_month_is_caveated_and_left_out_of_trend_and_anomalies(tmp_path):
    # Nine complete months around 100, then one tiny order on 2 September: a partial month
    # that would look like a huge drop.
    dates = [*months(2025, 12, 9), date(2026, 9, 2)]
    catalog = make_catalog(
        tmp_path, orders_frame(dates, [100, 102, 98, 101, 99, 103, 97, 100, 101, 5])
    )
    result = answer(catalog, MONTHLY_REVENUE)

    assert result.meta.partial_period == "2026-09-01"
    caveat = next(c for c in result.meta.caveats if c.code == "partial_period")
    assert caveat.message == "2026-09 only contains data up to 2026-09-02, so it is incomplete."
    assert result.table.rows[-1] == ["2026-09-01", 5.0]  # the row stays in the table

    composed = compose(result)
    trend = next(c for c in composed.claims if c.kind == "trend")
    assert trend.period == "2025-12-01 to 2026-08-01"
    assert trend.periods_covered == 9
    sentence = next(s for s in composed.answer_sentences if "increased" in s or "decreased" in s)
    assert sentence.endswith("from Dec 2025 to Aug 2026, across 9 periods.")
    assert validate_claims(composed) == composed.claims
    # A trend that runs into the partial month is not accepted.
    stretched = trend.model_copy(
        update={"period": "2025-12-01 to 2026-09-01", "periods_covered": 10}
    )
    assert stretched not in validate_claims(composed.model_copy(update={"claims": [stretched]}))

    # Without the exclusion the partial month is the outlier; with it, nothing is flagged.
    assert [f.row_key for f in detect(result.table, "revenue", [], "period").flags] == [
        {"period": "2026-09-01"}
    ]
    detect_result(result, "revenue")
    assert result.anomalies.flags == []
    assert result.anomalies.excluded_values == 1
    assert "incomplete final period" in result.anomalies.reason
    assert not any(f.row_key == {"period": "2026-09-01"} for f in result.anomalies.flags)


def test_data_ending_on_the_last_day_of_a_month_has_no_partial_period(tmp_path):
    dates = [*months(2025, 12, 3), date(2026, 3, 31)]
    catalog = make_catalog(tmp_path, orders_frame(dates, [10.0, 20.0, 30.0, 40.0]))
    result = answer(catalog, MONTHLY_REVENUE)
    assert result.meta.partial_period is None
    assert "partial_period" not in [c.code for c in result.meta.caveats]
    composed = compose(result)
    trend = next(c for c in composed.claims if c.kind == "trend")
    assert trend.period == "2025-12-01 to 2026-03-01" and trend.periods_covered == 4


def test_partial_period_respects_filters_and_grain(tmp_path):
    catalog = make_catalog(
        tmp_path, orders_frame([date(2026, 1, 10), date(2026, 3, 9)], [1.0, 2.0])
    )
    quarter = {
        **MONTHLY_REVENUE,
        "time_bucket": {**MONTHLY_REVENUE["time_bucket"], "grain": "quarter"},
    }
    meta = answer(catalog, quarter).meta
    assert meta.partial_period == "2026-01-01"
    assert next(c for c in meta.caveats if c.code == "partial_period").message == (
        "2026-Q1 only contains data up to 2026-03-09, so it is incomplete."
    )
    year = {**MONTHLY_REVENUE, "time_bucket": {**MONTHLY_REVENUE["time_bucket"], "grain": "year"}}
    assert answer(catalog, year).meta.partial_period == "2026-01-01"
    day = {**MONTHLY_REVENUE, "time_bucket": {**MONTHLY_REVENUE["time_bucket"], "grain": "day"}}
    assert answer(catalog, day).meta.partial_period is None
    no_bucket = dict(base_dataset="orders", metrics=MONTHLY_REVENUE["metrics"])
    assert answer(catalog, no_bucket).meta.partial_period is None


@pytest.mark.parametrize(
    ("day", "grain", "start", "end"),
    [
        (date(2026, 10, 2), "month", date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 2, 10), "month", date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 12, 5), "quarter", date(2026, 10, 1), date(2026, 12, 31)),
        (date(2026, 5, 5), "quarter", date(2026, 4, 1), date(2026, 6, 30)),
        (date(2026, 10, 2), "year", date(2026, 1, 1), date(2026, 12, 31)),
        (date(2026, 10, 2), "week", date(2026, 9, 28), date(2026, 10, 4)),  # Friday
        (date(2026, 10, 2), "day", date(2026, 10, 2), date(2026, 10, 2)),
    ],
)
def test_bucket_bounds(day, grain, start, end):
    assert _bucket_bounds(day, grain) == (start, end)


# ---- sentence wording ---------------------------------------------------------


def table_of(columns: list[tuple[str, ColumnRole]], rows: list[list]) -> ResultTable:
    return ResultTable(
        columns=[ResultColumn(name=n, dtype="VARCHAR", role=r) for n, r in columns], rows=rows
    )


def with_spec(table: ResultTable, metrics: list[dict], currency="GBP", **extra) -> AnalysisResult:
    s = spec(base_dataset="orders", metrics=metrics, **extra)
    meta = ExecutionMeta(
        sql="x",
        params=[],
        row_count=len(table.rows),
        truncated=False,
        elapsed_ms=1,
        dataset_versions={"id0": 1},
        currencies={"orders": currency} if currency else {},  # as the sample data declares
    )
    return AnalysisResult(
        id="r1",
        owner_id="u1",
        conversation_id="c1",
        question="q",
        outcome=Outcome.ok,
        spec=s,
        table=table,
        meta=meta,
        created_at=datetime.now(UTC),
    )


REVENUE = {"name": "revenue", "agg": "sum", "expr": revenue_expr()}
DIM = [ColumnRole.dimension, ColumnRole.metric]


def test_rank_sentence_uses_entity_value_and_gbp_with_two_decimals():
    table = table_of(
        [("region", DIM[0]), ("revenue", DIM[1])],
        [["West", 135306.2], ["East", 90000.0], ["North", 80000.5]],
    )
    composed = compose(with_spec(table, [REVENUE], dimensions=[ref("orders", "region")]))
    assert composed.answer_sentences[0] == (
        "West had the highest revenue: £135,306.20 among 3 returned groups."
    )
    assert all("region=" not in s for s in composed.answer_sentences)
    assert composed.claims[0].unit == "GBP"
    assert validate_claims(composed) == composed.claims


def test_multiple_dimensions_render_as_comma_separated_values():
    table = table_of(
        [
            ("region", ColumnRole.dimension),
            ("channel", ColumnRole.dimension),
            ("revenue", ColumnRole.metric),
        ],
        [["West", "web", 1234.5]],
    )
    composed = compose(
        with_spec(table, [REVENUE], dimensions=[ref("orders", "region"), ref("orders", "channel")])
    )
    assert "West, web: revenue was £1,234.50." in composed.answer_sentences


def test_counts_are_integers_and_unitless_metrics_keep_plain_numbers():
    count = table_of([("region", DIM[0]), ("orders", DIM[1])], [["West", 1234.0], ["East", 3.0]])
    composed = compose(
        with_spec(count, [{"name": "orders", "agg": "count"}], dimensions=[ref("orders", "region")])
    )
    assert (
        composed.answer_sentences[0]
        == "West had the highest orders: 1,234 among 2 returned groups."
    )
    assert composed.claims[0].unit == "count"

    plain = table_of([("region", DIM[0]), ("avg_qty", DIM[1])], [["West", 2.5], ["East", 1.0]])
    composed = compose(
        with_spec(
            plain,
            [
                {
                    "name": "avg_qty",
                    "agg": "avg",
                    "expr": {"kind": "col", "ref": ref("orders", "quantity")},
                }
            ],
            dimensions=[ref("orders", "region")],
        )
    )
    assert (
        composed.answer_sentences[0] == "West had the highest avg qty: 2.5 among 2 returned groups."
    )
    assert composed.claims[0].unit is None


def test_trend_and_percentage_sentences_use_the_metric_unit():
    table = table_of(
        [("period", ColumnRole.time), ("revenue", ColumnRole.metric)],
        [["2026-01-01", 1000.0], ["2026-02-01", 2000.0], ["2026-03-01", 2500.0]],
    )
    composed = compose(
        with_spec(
            table, [REVENUE], time_bucket={"column": ref("orders", "order_date"), "grain": "month"}
        )
    )
    assert (
        "The selected data: revenue increased by £1,500.00 from Jan 2026 to Mar 2026, "
        "across 3 periods." in composed.answer_sentences
    )
    assert (
        "The selected data: revenue increased by 25% in Mar 2026 versus the previous "
        "observed period." in composed.answer_sentences
    )
    assert validate_claims(composed) == composed.claims


def test_a_model_supplied_unit_is_still_rejected():
    table = table_of([("region", DIM[0]), ("revenue", DIM[1])], [["West", 10.0]])
    composed = compose(with_spec(table, [REVENUE], dimensions=[ref("orders", "region")]))
    forged = composed.claims[0].model_copy(update={"unit": "USD"})
    assert validate_claims(composed.model_copy(update={"claims": [forged]})) == []
    bare = composed.claims[0].model_copy(update={"unit": None})
    assert validate_claims(composed.model_copy(update={"claims": [bare]})) == []


def test_periods_read_by_grain_and_the_first_months_are_not_narrated():
    from app.agent.composer import _period

    assert [_period("2026-01-01", g) for g in ("month", "quarter", "year", "week", "day")] == [
        "Jan 2026", "Q1 2026", "2026", "week of 1 Jan 2026", "1 Jan 2026"]
    assert _period("not a date", "month") == "not a date"
    table = table_of(
        [("period", ColumnRole.time), ("revenue", ColumnRole.metric)],
        [["2026-01-01", 1000.0], ["2026-02-01", 2000.0], ["2026-03-01", 2500.0]],
    )
    composed = compose(
        with_spec(
            table, [REVENUE], time_bucket={"column": ref("orders", "order_date"), "grain": "month"}
        )
    )
    # Trend + latest change only; "revenue in Jan 2026 was ..." filler is not narrated.
    assert not any(" was " in s for s in composed.answer_sentences)
    assert len(composed.answer_sentences) == 2


def test_a_limited_time_series_still_gets_value_sentences():
    table = table_of(
        [("period", ColumnRole.time), ("revenue", ColumnRole.metric)],
        [["2026-01-01", 1000.0], ["2026-02-01", 2000.0]],
    )
    composed = compose(
        with_spec(
            table, [REVENUE], limit=2,
            time_bucket={"column": ref("orders", "order_date"), "grain": "month"},
        )
    )
    assert "The selected data: revenue in Jan 2026 was £1,000.00." in composed.answer_sentences


def test_money_without_a_declared_currency_has_no_symbol():
    table = table_of([("region", DIM[0]), ("revenue", DIM[1])], [["West", 79.0]])
    composed = compose(
        with_spec(table, [REVENUE], currency=None, dimensions=[ref("orders", "region")])
    )
    assert composed.claims[0].unit is None
    assert all("£" not in s and "$" not in s for s in composed.answer_sentences)
    assert "79" in " ".join(composed.answer_sentences)


def test_a_declared_currency_other_than_gbp_is_used():
    table = table_of([("region", DIM[0]), ("revenue", DIM[1])], [["West", 79.0]])
    composed = compose(
        with_spec(table, [REVENUE], currency="USD", dimensions=[ref("orders", "region")])
    )
    assert "West had the highest revenue: $79.00" in composed.answer_sentences[0]
