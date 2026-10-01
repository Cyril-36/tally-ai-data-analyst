from datetime import UTC, datetime

import pytest

from app.agent.composer import compose
from app.agent.validator import validate_claims
from app.charts.builder import default_chart, figure_json, validate_chart
from app.contracts.outcomes import Outcome
from app.contracts.result import (
    AnalysisResult,
    Caveat,
    ChartSpec,
    ChartType,
    ColumnRole,
    ExecutionMeta,
    ResultColumn,
    ResultTable,
)
from app.reports.builder import csv_report, html_report


def result() -> AnalysisResult:
    table = ResultTable(
        columns=[
            ResultColumn(name="region", dtype="VARCHAR", role=ColumnRole.dimension),
            ResultColumn(name="period", dtype="DATE", role=ColumnRole.time),
            ResultColumn(name="revenue", dtype="DOUBLE", role=ColumnRole.metric),
        ],
        rows=[
            ["<West>", datetime(2026, 1, 1).date(), 10.0],
            ["East", datetime(2026, 2, 1).date(), 20.0],
        ],
    )
    return AnalysisResult(
        id="r1",
        owner_id="u1",
        conversation_id="c1",
        question="Show <sales>",
        outcome=Outcome.ok,
        table=table,
        meta=ExecutionMeta(
            sql="SELECT '<script>'",
            params=[],
            row_count=2,
            truncated=False,
            elapsed_ms=1,
            dataset_versions={"d1": 1},
        ),
        created_at=datetime.now(UTC),
    )


def test_composer_claims_and_validator_rederive_row_identity_and_period():
    composed = compose(result())
    assert len(composed.claims) == 2
    assert "region=<West>" in composed.answer_sentences[0]
    assert "2026-01-01" in composed.answer_sentences[0]
    assert validate_claims(composed) == composed.claims

    original = composed.claims[0]
    composed.claims = [original.model_copy(update={"entity": {"region": "East"}})]
    assert validate_claims(composed) == []
    composed.claims = [original.model_copy(update={"period": "2026-02-01"})]
    assert validate_claims(composed) == []
    composed.claims = [original.model_copy(update={"unit": "USD"})]
    assert validate_claims(composed) == []


def test_period_comparison_is_recomputed_from_the_last_two_observed_periods():
    data = result()
    data.table.rows = [
        ["West", "2026-01-01", 10.0],
        ["West", "2026-02-01", 20.0],
        ["West", "2026-03-01", 30.0],
    ]
    composed = compose(data)
    comparisons = [claim for claim in composed.claims if claim.kind == "comparison"]
    assert len(comparisons) == 1
    assert comparisons[0].value == 50.0 and comparisons[0].unit == "%"
    assert any(
        "50%" in sentence and "previous observed period" in sentence
        for sentence in composed.answer_sentences
    )
    bad = comparisons[0].model_copy(update={"period": "2026-02-01"})
    tampered = composed.model_copy(update={"claims": [*composed.claims[:-1], bad]})
    assert bad not in validate_claims(tampered)


def test_chart_validation_and_figure_use_only_result_rows():
    data = result().table
    spec = default_chart(data)
    assert spec and spec.type == ChartType.line
    encoded = figure_json(data, spec)
    assert [trace["y"][0] for trace in encoded["data"]] == [10.0, 20.0]
    assert [trace["x"][0] for trace in encoded["data"]] == ["2026-01-01", "2026-02-01"]
    with pytest.raises(ValueError, match="must exist"):
        validate_chart(data, ChartSpec(type="bar", x="missing", y=["revenue"], title="x"))


def test_pie_rejects_negative_values_and_multiple_metrics():
    data = ResultTable(
        columns=[
            ResultColumn(name="region", dtype="VARCHAR", role=ColumnRole.dimension),
            ResultColumn(name="a", dtype="DOUBLE", role=ColumnRole.metric),
            ResultColumn(name="b", dtype="DOUBLE", role=ColumnRole.metric),
        ],
        rows=[["West", -1, 2]],
    )
    with pytest.raises(ValueError, match="one metric"):
        validate_chart(data, ChartSpec(type="pie", x="region", y=["a", "b"], title="x"))
    with pytest.raises(ValueError, match="negative"):
        validate_chart(data, ChartSpec(type="pie", x="region", y=["a"], title="x"))


def test_bar_pie_and_scatter_plot_the_stored_numbers():
    grouped = ResultTable(
        columns=[
            ResultColumn(name="region", dtype="VARCHAR", role=ColumnRole.dimension),
            ResultColumn(name="revenue", dtype="DOUBLE", role=ColumnRole.metric),
            ResultColumn(name="units", dtype="BIGINT", role=ColumnRole.metric),
        ],
        rows=[["West", 10.0, 2], ["East", 20.0, 4]],
    )
    for chart_type in ("bar", "pie"):
        spec = ChartSpec(type=chart_type, x="region", y=["revenue"], title="Result")
        figure = figure_json(grouped, validate_chart(grouped, spec))
        trace = figure["data"][0]
        values = trace["values"] if chart_type == "pie" else trace["y"]
        assert values == [10.0, 20.0]
    scatter = ChartSpec(type="scatter", x="units", y=["revenue"], title="Result")
    figure = figure_json(grouped, validate_chart(grouped, scatter))
    assert figure["data"][0]["x"] == [2, 4]
    assert figure["data"][0]["y"] == [10.0, 20.0]


def test_reports_escape_html_protect_csv_formulas_and_show_sql_caveats_status():
    data = result()
    data.meta.caveats = [Caveat(code="notice", message="<img src=x>", count=1)]
    page = html_report([data], {"r1": "stale — refresh required"})
    assert "&lt;script&gt;" in page and "&lt;img src=x&gt;" in page
    assert "stale — refresh required" in page
    assert "SELECT &#x27;&lt;script&gt;&#x27;" in page

    csv_data = result()
    csv_data.table.rows[0][0] = "=cmd|x"
    exported = csv_report(csv_data)
    assert "'=cmd|x" in exported
    assert "SELECT '<script>'" not in exported  # CSV is a table export, not a report.
