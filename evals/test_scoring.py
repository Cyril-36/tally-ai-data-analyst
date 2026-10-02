"""The live score must reject plausible-looking but incoherent results."""

from datetime import UTC, datetime

from cases import Case
from run_live import _same_table, _score

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
