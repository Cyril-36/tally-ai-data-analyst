"""Quality report: each planted issue in the fixtures is found, clean data scores well,
and the report never touches the stored data."""

import hashlib
from datetime import date
from pathlib import Path

import pytest

from app.datasets.ingest import ingest_csv
from app.datasets.quality import quality_report

FIXTURES = Path(__file__).parent / "fixtures"
REF = date(2026, 9, 30)


def report_for(tmp_path: Path, name: str) -> dict:
    out = tmp_path / f"{name}.parquet"
    profile = ingest_csv((FIXTURES / name).read_bytes(), name, out)
    return quality_report(out, profile, REF)


def issues(report: dict, code: str) -> dict[str | None, dict]:
    return {i["column"]: i for i in report["issues"] if i["code"] == code}


@pytest.fixture(scope="module")
def messy(tmp_path_factory) -> dict:
    return report_for(tmp_path_factory.mktemp("q"), "messy_customers.csv")


def test_nulls_are_reported_per_column(messy):
    nulls = issues(messy, "nulls")
    assert {c: i["count"] for c, i in nulls.items()} == {
        "customer_name": 2, "segment": 6, "email": 8}  # segment: 5 nulled + a nulled duplicate
    by_col = {c["name"]: c for c in messy["columns"]}
    assert by_col["email"]["null_count"] == 8 and by_col["email"]["null_pct"] > 10


def test_duplicate_rows_are_counted(messy):
    assert messy["duplicate_rows"] == 3
    assert issues(messy, "duplicate_rows")[None]["count"] == 3


def test_numeric_looking_text_column_is_found_with_examples(messy):
    found = {c["column"]: c for c in messy["numeric_text_columns"]}
    assert set(found) == {"lifetime_value"}
    assert found["lifetime_value"]["non_numeric_count"] == 4
    assert {"n/a", "approx 300", "unknown"} == set(found["lifetime_value"]["non_numeric_examples"])
    assert "lifetime_value" in issues(messy, "numeric_stored_as_text")


def test_category_variants_are_grouped(messy):
    (entry,) = messy["category_variants"]
    assert entry["column"] == "country"
    uk = next(g for g in entry["groups"] if "UK" in g)
    assert set(uk) == {"UK", "uk ", "U.K."}
    assert any(set(g) == {"France", "FRANCE"} for g in entry["groups"])


def test_future_dates_are_found_against_the_reference_date(messy):
    assert messy["future_dates"] == [{"column": "signup_date", "count": 2}]
    assert messy["reference_date"] == "2026-09-30"


def test_future_date_check_follows_the_reference_date(tmp_path):
    out = tmp_path / "m.parquet"
    profile = ingest_csv((FIXTURES / "messy_customers.csv").read_bytes(), "m.csv", out)
    later = quality_report(out, profile, date(2030, 1, 1))
    assert later["future_dates"] == []


def test_score_is_in_range_and_explained_by_penalties(messy):
    assert 0 <= messy["score"] < 80
    assert messy["score"] == round(100 - sum(messy["penalties"].values()))
    assert all(v > 0 for v in messy["penalties"].values())


def test_clean_orders_score_high_and_negatives_are_info_only(tmp_path):
    report = report_for(tmp_path, "orders.csv")
    assert report["score"] >= 95
    assert report["duplicate_rows"] == 1  # the planted duplicate order
    assert report["future_dates"] == [] and report["category_variants"] == []
    assert "order_id" not in report["candidate_keys"]  # the duplicate breaks uniqueness
    negative = issues(report, "negative_values")["quantity"]
    assert negative["level"] == "info" and negative["count"] == 1


def test_candidate_keys_on_a_dimension_table(tmp_path):
    report = report_for(tmp_path, "customers.csv")
    assert {"customer_id", "email"} <= set(report["candidate_keys"])
    assert report["score"] == 100


def test_report_does_not_modify_data(tmp_path):
    out = tmp_path / "m.parquet"
    profile = ingest_csv((FIXTURES / "messy_customers.csv").read_bytes(), "m.csv", out)
    before = hashlib.sha256(out.read_bytes()).hexdigest()
    quality_report(out, profile, REF)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == before


def test_one_duplicate_in_many_rows_never_scores_100(tmp_path):
    rows = "\n".join(f"{i},{i * 2}" for i in range(5000))
    data = f"id,amount\n{rows}\n0,0\n".encode()  # exactly one duplicate row
    profile = ingest_csv(data, "big.csv", tmp_path / "v1.parquet")
    report = quality_report(tmp_path / "v1.parquet", profile, REF)
    assert report["duplicate_rows"] == 1
    assert report["score"] == 99
