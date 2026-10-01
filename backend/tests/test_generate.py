"""The sample-data generator: determinism, and that ANOMALIES.md (the ground truth the
evals score against) agrees with the CSVs it describes."""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
GENERATOR = REPO / "data" / "generate.py"
FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = REPO / "data" / "sample"
OUTPUTS = ["orders.csv", "customers.csv", "products.csv", "payments.csv", "messy_customers.csv",
           "ANOMALIES.md"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(out_dir: Path, *args: str) -> None:
    subprocess.run([sys.executable, str(GENERATOR), "--out-dir", str(out_dir), *args],
                   check=True, capture_output=True)


@pytest.fixture(scope="module")
def truth() -> dict:
    md = (FIXTURES / "ANOMALIES.md").read_text(encoding="utf-8")
    return json.loads(re.search(r"```json\n(.*?)\n```", md, re.S).group(1))


@pytest.fixture(scope="module")
def orders() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "orders.csv")
    df["revenue"] = df.quantity * df.unit_price - df.discount_amount
    df["month"] = df.order_date.str[:7]
    return df


def test_two_runs_are_byte_identical_and_match_committed_files(tmp_path):
    _run(tmp_path / "a")
    _run(tmp_path / "b")
    for name in OUTPUTS:
        assert _sha(tmp_path / "a" / name) == _sha(tmp_path / "b" / name), name
        assert _sha(tmp_path / "a" / name) == _sha(FIXTURES / name), f"fixtures/{name} is stale"
        assert _sha(tmp_path / "a" / name) == _sha(SAMPLE / name), f"sample/{name} is stale"


def test_other_reference_date_keeps_anomalies_and_ends_on_that_date(tmp_path):
    _run(tmp_path, "--reference-date", "2026-10-15")
    df = pd.read_csv(tmp_path / "orders.csv")
    assert df.order_date.max() == "2026-10-15"
    assert (tmp_path / "ANOMALIES.md").read_text().count("2026-10-15") >= 1


def test_orders_shape(orders):
    assert 4500 <= len(orders) <= 5500
    assert orders.order_date.min() >= "2025-01-01" and orders.order_date.max() <= "2026-09-30"
    assert orders.month.nunique() == 21
    assert set(orders.region) == {"North", "South", "East", "West", "Central"}
    assert list(pd.read_csv(FIXTURES / "orders.csv").columns) == [
        "order_id", "order_date", "customer_id", "product_id", "region", "quantity",
        "unit_price", "discount_amount"]


def test_dimension_keys_are_unique_and_orders_join(orders):
    customers = pd.read_csv(FIXTURES / "customers.csv")
    products = pd.read_csv(FIXTURES / "products.csv")
    assert customers.customer_id.is_unique and products.product_id.is_unique
    assert customers.email.str.endswith("@example.com").all()
    assert set(orders.customer_id) <= set(customers.customer_id)
    assert set(orders.product_id) <= set(products.product_id)


def test_payments_fan_out_and_add_up(orders):
    payments = pd.read_csv(FIXTURES / "payments.csv")
    per_order = payments.groupby("order_id").amount.agg(["count", "sum"])
    split_share = (per_order["count"] == 2).mean()
    assert 0.05 <= split_share <= 0.15
    revenue = orders.drop_duplicates("order_id").set_index("order_id").revenue
    assert len(per_order) == len(revenue)
    assert (per_order["sum"].round(2) == revenue.round(2).loc[per_order.index]).all()


def test_west_spike_matches_data(orders, truth):
    t = truth["west_spike"]
    west = orders[orders.region == "West"].groupby("month").revenue.sum().round(2)
    assert west[t["month"]] == t["revenue"]
    assert t["revenue"] == pytest.approx(3 * t["revenue_without_spike"], abs=0.01)
    median = west.median()  # the default reference date is a month end: all 21 months complete
    mad = (west - median).abs().median()
    robust_z = (west - median) / (1.4826 * mad)
    assert robust_z.idxmax() == t["month"] and robust_z.max() > 3.5


def test_south_drop_matches_data(orders, truth):
    t = truth["south_drop"]
    south = orders[orders.region == "South"].groupby("month").revenue.sum().round(2)
    assert south[t["month"]] == t["revenue"]
    assert south.idxmin() == t["month"]
    others = south.drop(t["month"])
    assert t["revenue"] < 0.6 * others.median()
    assert t["median_other_months"] == pytest.approx(others.median(), abs=0.01)


def test_negative_quantity_and_duplicate_rows_match_data(orders, truth):
    negative = orders[orders.quantity < 0]
    assert list(negative.order_id) == [truth["negative_quantity"]["order_id"]]
    assert int(negative.quantity.iloc[0]) == truth["negative_quantity"]["quantity"]

    dups = orders[orders.duplicated(keep=False)]
    assert list(dups.order_id.unique()) == [truth["duplicate_order"]["order_id"]]
    assert len(dups) == truth["duplicate_order"]["copies"] == 2


def test_underperforming_product_declines_every_month(orders, truth):
    t = truth["underperforming_product"]
    by_month = orders[orders.product_id == t["product_id"]].groupby("month").revenue.sum().round(2)
    claimed = t["monthly_revenue"]
    assert list(claimed) == sorted(claimed) and len(claimed) == 6
    for month, revenue in claimed.items():
        assert by_month[month] == revenue
    values = list(claimed.values())
    assert all(a > b for a, b in zip(values, values[1:], strict=False))


def test_messy_customers_matches_truth(truth):
    t = truth["messy_customers"]
    df = pd.read_csv(FIXTURES / "messy_customers.csv", dtype=str, keep_default_na=False,
                     na_values=[""])
    assert len(df) == t["rows"] and df.duplicated().sum() == t["duplicate_rows"] == 3
    assert {c: int(v) for c, v in df.isna().sum().items() if v} == t["null_counts"]
    assert pd.to_numeric(df.lifetime_value, errors="coerce").isna().sum() == \
        t["non_numeric_lifetime_value"]
    assert (df.signup_date > truth["reference_date"]).sum() == t["future_signup_dates"] == 2
    assert {"UK", "uk ", "U.K."} <= set(df.country)


def test_bad_fixtures_are_what_they_claim():
    bad = FIXTURES / "bad"
    assert (bad / "empty.csv").read_bytes() == b""
    assert (bad / "header_only.csv").read_text().count("\n") == 1
    with pytest.raises(UnicodeDecodeError):
        (bad / "latin1.csv").read_bytes().decode("utf-8")
    assert b";" in (bad / "semicolon.csv").read_bytes()
    assert b"ignore previous instructions" in (bad / "prompt_injection.csv").read_bytes().lower()
