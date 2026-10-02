"""The live score must reject plausible-looking but incoherent results."""

from datetime import UTC, datetime

from cases import REGION, Case, spec
from run_live import _answer_table_matches, _same_spec, _same_table, _score

from app.agent.composer import compose
from app.charts.builder import default_chart
from app.contracts.outcomes import Outcome
from app.contracts.result import AnalysisResult, ColumnRole, ResultColumn, ResultTable


def _answer() -> dict:
    table = ResultTable(
        columns=[
            ResultColumn(name="region", dtype="VARCHAR", role=ColumnRole.dimension),
            ResultColumn(name="revenue", dtype="DOUBLE", role=ColumnRole.metric),
        ],
        rows=[["West", 100.0], ["East", 80.0]],
    )
    result = compose(
        AnalysisResult(
            id="r1",
            owner_id="u1",
            conversation_id="c1",
            question="Show revenue by region",
            outcome=Outcome.ok,
            table=table,
            chart=default_chart(table),
            created_at=datetime.now(UTC),
        )
    )
    return result.model_dump(mode="json")


def test_score_binds_numbers_to_rows_and_sentences():
    actual = _answer()
    expected = actual["table"]
    case = Case("revenue_region", "Show revenue by region")
    assert _score(case, actual, expected, {})["correct"] is True

    swapped = [row.copy() for row in expected["rows"]]
    swapped[0][1], swapped[1][1] = swapped[1][1], swapped[0][1]
    assert _same_table({**expected, "rows": swapped}, expected) is False

    wrong_sentence = {**actual, "answer_sentences": ["East earned 100."]}
    assert _score(case, wrong_sentence, expected, {})["claims_valid"] is False

    wrong_claim = {**actual, "claims": [*actual["claims"]]}
    wrong_claim["claims"][0] = {**wrong_claim["claims"][0], "value": 999.0}
    assert _score(case, wrong_claim, expected, {})["claims_valid"] is False


def test_answer_score_accepts_extra_metrics_but_rejects_wrong_calculation():
    expected_spec = spec("revenue", dimensions=REGION)
    expected = {
        "columns": [
            {"name": "region", "role": "dimension"},
            {"name": "revenue", "role": "metric"},
        ],
        "rows": [["East", 80.0], ["West", 100.0]],
    }
    actual_spec = {**expected_spec, "metrics": [
        {**expected_spec["metrics"][0], "name": "sales"},
        {"name": "units", "agg": "sum", "expr": {"kind": "col", "ref": {
            "dataset": "orders", "column": "quantity",
        }}},
    ]}
    actual = {
        "spec": actual_spec,
        "table": {"columns": [
            {"name": "region", "role": "dimension"},
            {"name": "sales", "role": "metric"},
            {"name": "units", "role": "metric"},
        ], "rows": [["West", 100.0, 7], ["East", 80.0, 4]]},
    }
    assert _same_spec(actual_spec, expected_spec) is False
    assert _answer_table_matches(actual, expected_spec, expected) is True
    wrong = {**actual, "table": {**actual["table"], "rows": [
        ["West", 80.0, 7], ["East", 100.0, 4],
    ]}}
    assert _answer_table_matches(wrong, expected_spec, expected) is False
    wrong_metric = {**actual, "spec": {**actual_spec, "metrics": [
        {**actual_spec["metrics"][0], "agg": "avg"}, actual_spec["metrics"][1],
    ]}}
    assert _answer_table_matches(wrong_metric, expected_spec, expected) is False


def test_anomaly_score_requires_complete_flags_with_correct_row_values():
    truth = {
        "west_spike": {"region": "West", "month": "2026-09"},
        "south_drop": {"region": "South", "month": "2026-09"},
    }
    expected = {
        "columns": [
            {"name": "region", "role": "dimension"},
            {"name": "period", "role": "time"},
            {"name": "revenue", "role": "metric"},
        ],
        "rows": [["West", "2026-09-01", 100.0], ["South", "2026-09-01", 5.0]],
    }
    flags = [
        {
            "row_key": {"region": "West", "period": "2026-09-01"},
            "metric": "revenue",
            "value": 100.0,
        },
        {"row_key": {"region": "South", "period": "2026-09-01"}, "metric": "revenue", "value": 5.0},
    ]
    case = Case("anomalies", "Find anomalies", anomaly_metric="revenue")
    actual = {"outcome": "ok", "anomalies": {"status": "completed_found", "flags": flags}}
    score = _score(case, actual, None, truth)
    assert score["anomaly_precision"] == score["anomaly_recall"] == 1.0
    assert score["anomaly_evidence_valid"] is False  # no baseline table to verify values

    # The real score uses the baseline table. Isolate its flag-evidence rule from claims.
    actual = _answer()
    actual["table"] = expected
    actual["anomalies"] = {"status": "completed_found", "flags": flags}
    score = _score(case, actual, expected, truth)
    assert score["anomaly_evidence_valid"] is True
    actual["anomalies"]["flags"][0]["value"] = 80.0
    assert _score(case, actual, expected, truth)["anomaly_evidence_valid"] is False
    actual["anomalies"]["flags"] = flags[:1]
    assert _score(case, actual, expected, truth)["anomaly_recall"] == 0.5


def test_answer_score_requires_the_requested_order_and_limit():
    expected_spec = {**spec("revenue", dimensions=REGION),
                     "sort": [{"by": "revenue", "descending": False}], "limit": 1}
    expected = {
        "columns": [
            {"name": "region", "role": "dimension"},
            {"name": "revenue", "role": "metric"},
        ],
        "rows": [["East", 80.0]],
    }
    good_spec = {**expected_spec, "metrics": [{**expected_spec["metrics"][0], "name": "sales"}],
                 "sort": [{"by": "sales", "descending": False}]}
    table = {"columns": [{"name": "region", "role": "dimension"},
                         {"name": "sales", "role": "metric"}], "rows": [["East", 80.0]]}
    assert _answer_table_matches({"spec": good_spec, "table": table}, expected_spec, expected)
    flipped = {**good_spec, "sort": [{"by": "sales", "descending": True}]}
    assert not _answer_table_matches({"spec": flipped, "table": table}, expected_spec, expected)
    unlimited = {**good_spec, "limit": None}
    assert not _answer_table_matches({"spec": unlimited, "table": table}, expected_spec, expected)


def test_a_harmless_limit_on_an_unlimited_question_is_accepted():
    expected_spec = spec("revenue", dimensions=REGION)
    expected = {"columns": [{"name": "region", "role": "dimension"},
                            {"name": "revenue", "role": "metric"}],
                "rows": [["East", 80.0], ["West", 100.0]]}
    capped = {**expected_spec, "limit": 1000}
    table = {"columns": expected["columns"], "rows": [["West", 100.0], ["East", 80.0]]}
    assert _answer_table_matches({"spec": capped, "table": table}, expected_spec, expected)
    cut = {"spec": {**expected_spec, "limit": 1},
           "table": {"columns": expected["columns"], "rows": [["West", 100.0]]}}
    assert not _answer_table_matches(cut, expected_spec, expected)


def test_usage_summary_reports_latency_tokens_and_cost_only_when_priced(monkeypatch):
    from run_live import usage_summary

    cases = [
        {"latency_ms": 1000, "tokens": 300, "prompt_tokens": 200, "completion_tokens": 100},
        {"latency_ms": 3000, "tokens": 600, "prompt_tokens": 500, "completion_tokens": 100},
        {"id": "skipped", "status": "not_run_provider_unavailable"},
    ]
    monkeypatch.delenv("LLM_PRICE_INPUT_PER_M", raising=False)
    plain = usage_summary(cases)
    assert plain["cases_run"] == 2 and plain["latency_ms"]["max"] == 3000
    assert plain["tokens_total"] == 900 and plain["cost"] is None
    monkeypatch.setenv("LLM_PRICE_INPUT_PER_M", "0.3")
    monkeypatch.setenv("LLM_PRICE_OUTPUT_PER_M", "2.5")
    priced = usage_summary(cases)["cost"]
    assert priced["total"] == round((700 * 0.3 + 200 * 2.5) / 1_000_000, 4)
