"""The automatic overview dashboard: schema-driven cards over the normal validated path."""

from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).parents[2]
SAMPLE = ROOT / "data" / "sample"
TRANSFER = ROOT / "evals" / "transfer"


def upload(client, path: Path) -> dict:
    with open(path, "rb") as f:
        r = client.post("/api/datasets", files=[("files", (path.name, f, "text/csv"))])
    assert r.status_code == 201, r.text
    return r.json()[0]


def overview(client, dataset_id: str) -> dict:
    r = client.post(f"/api/datasets/{dataset_id}/overview")
    assert r.status_code == 200, r.text
    return r.json()


def card(data: dict, kind: str) -> dict:
    return next(c["result"] for c in data["cards"] if c["kind"] == kind)


def column(result: dict, role: str) -> list:
    i = next(i for i, c in enumerate(result["table"]["columns"]) if c["role"] == role)
    return [row[i] for row in result["table"]["rows"]]


@pytest.mark.parametrize(
    ("path", "metric", "dimension"),
    [
        (TRANSFER / "support_cases.csv", "resolution_minutes", "team"),
        (TRANSFER / "subscriptions.csv", "monthly_fee", "plan"),
    ],
)
def test_overview_works_on_unrelated_schemas(make_client, path, metric, dimension):
    client = make_client()
    data = overview(client, upload(client, path)["id"])
    frame = pd.read_csv(path)
    assert [c["kind"] for c in data["cards"]] == ["totals", "trend", "breakdown", "mix"]
    assert all(c["result"]["outcome"] == "ok" for c in data["cards"])

    totals = card(data, "totals")
    assert totals["table"]["rows"][0][0] == len(frame)
    assert totals["table"]["rows"][0][1] == pytest.approx(frame[metric].sum())

    breakdown = card(data, "breakdown")
    expected = frame.groupby(dimension)[metric].sum().sort_values(ascending=False)
    assert column(breakdown, "dimension") == list(expected.index)
    assert column(breakdown, "metric") == pytest.approx(list(expected.values))
    assert breakdown["chart"] is not None

    trend = card(data, "trend")
    assert sum(column(trend, "metric")) == pytest.approx(frame[metric].sum())
    assert trend["anomalies"] is not None  # checked, even if there is too little data to flag
    # No currency is declared on these uploads, so no money symbol appears anywhere.
    assert not any("£" in s for c in data["cards"] for s in c["result"]["answer_sentences"])


def test_overview_on_the_retail_sample_uses_monthly_buckets_and_gbp(make_client):
    client = make_client()
    loaded = client.post("/api/datasets/sample").json()["created"]
    orders = next(d for d in loaded if d["name"] == "orders")
    data = overview(client, orders["id"])
    trend = card(data, "trend")
    assert trend["spec"]["time_bucket"]["grain"] == "month"
    assert trend["anomalies"]["status"] in ("completed_none", "completed_found")
    assert trend["anomalies"]["series_checked"] == 1  # one total series, not per region
    assert any("£" in s for s in card(data, "totals")["answer_sentences"])


def test_cards_the_data_cannot_support_are_skipped_with_a_reason(make_client):
    client = make_client()
    loaded = {d["name"]: d for d in client.post("/api/datasets/sample").json()["created"]}
    products = overview(client, loaded["products"]["id"])  # no date column
    assert "trend" not in [c["kind"] for c in products["cards"]]
    assert any(s["kind"] == "trend" and "date" in s["reason"] for s in products["skipped"])
    teams = overview(client, upload(client, TRANSFER / "support_teams.csv")["id"])
    totals = card(teams, "totals")  # no numeric column: a row count only
    assert [c["name"] for c in totals["table"]["columns"]] == ["rows"]


def test_overview_is_reused_until_the_data_or_currency_changes(make_client):
    client = make_client()
    loaded = {d["name"]: d for d in client.post("/api/datasets/sample").json()["created"]}
    orders = loaded["orders"]["id"]
    first = overview(client, orders)
    again = overview(client, orders)
    assert again["reused"] and [c["result"]["id"] for c in again["cards"]] == [
        c["result"]["id"] for c in first["cards"]]
    conversations = [c for c in client.get("/api/conversations").json()
                     if c["title"].startswith("Overview")]
    assert len(conversations) == 1  # one overview conversation per dataset

    client.patch(f"/api/datasets/{orders}", json={"currency": "USD"})
    rebuilt = overview(client, orders)
    assert not rebuilt["reused"]
    assert any("$" in s for s in card(rebuilt, "totals")["answer_sentences"])


def test_overview_needs_no_ai_and_respects_ownership(make_client):
    admin = make_client("admin@example.com", role="admin")
    admin.put("/api/admin/settings", json={"max_upload_mb": 50, "llm_enabled": False})
    owner = make_client()
    dataset = upload(owner, TRANSFER / "support_cases.csv")
    assert overview(owner, dataset["id"])["cards"]  # AI is off; the overview still works
    for other in (make_client("someone-else@example.com"), admin):
        r = other.post(f"/api/datasets/{dataset['id']}/overview")
        assert r.status_code == 404


def test_non_additive_measures_are_averaged_not_summed(make_client):
    client = make_client()
    loaded = {d["name"]: d for d in client.post("/api/datasets/sample").json()["created"]}
    totals = card(overview(client, loaded["orders"]["id"]), "totals")
    names = [c["name"] for c in totals["table"]["columns"]]
    assert names == ["rows", "total_quantity", "average_unit_price"]
    orders = pd.read_csv(SAMPLE / "orders.csv")
    assert totals["table"]["rows"][0][2] == pytest.approx(orders.unit_price.mean())
    assert f"The selected data: {len(orders):,} rows." in totals["answer_sentences"]
