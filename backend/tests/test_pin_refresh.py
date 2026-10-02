"""Refreshing a pin re-runs its stored calculation; it never needs the AI."""

from pathlib import Path

import pytest

from tests.test_fake_provider import REGION_Q, SAMPLE, ask, fake, workspace  # noqa: F401

TRANSFER = Path(__file__).parents[2] / "evals" / "transfer"


def _ai_off(make_client):
    admin = make_client("admin@example.com", role="admin")
    assert admin.put("/api/admin/settings",
                     json={"max_upload_mb": 50, "llm_enabled": False}).status_code == 200


def test_stale_overview_pin_refreshes_with_ai_off(make_client):
    # Review finding: the refresh button used the AI question route (503 with AI off).
    client = make_client()
    with open(TRANSFER / "subscriptions.csv", "rb") as f:
        dataset = client.post("/api/datasets", files=[("files", ("subs.csv", f, "text/csv"))]
                              ).json()[0]
    card = client.post(f"/api/datasets/{dataset['id']}/overview").json()["cards"][0]["result"]
    pin = client.post("/api/pins", json={"result_id": card["id"]}).json()
    client.patch(f"/api/datasets/{dataset['id']}", json={"currency": "USD"})
    _ai_off(make_client)
    assert next(p for p in client.get("/api/dashboard").json() if p["id"] == pin["id"]
                )["status"] == "stale"

    refreshed = client.post(f"/api/pins/{pin['id']}/refresh")
    assert refreshed.status_code == 200, refreshed.text
    item = refreshed.json()
    assert item["id"] == pin["id"] and item["result_id"] != card["id"]
    assert item["status"] == "current"
    assert item["result"]["spec"] == card["spec"]  # the same calculation, not a reinterpretation
    assert any("$" in s for s in item["result"]["answer_sentences"])
    dashboard = {p["id"]: p for p in client.get("/api/dashboard").json()}
    assert dashboard[pin["id"]]["status"] == "current"


def test_question_pin_refresh_keeps_spec_chart_and_anomaly_check(workspace, make_client,  # noqa: F811
                                                                  monkeypatch):
    client, conversation_id = workspace
    saved = ask(client, conversation_id, "Detect anomalies in revenue by month").json()
    assert client.patch(f"/api/results/{saved['id']}/chart", json={"chart": {
        "type": "area", "x": "period", "y": ["revenue"], "series": "region",
        "title": "Revenue"}}).status_code == 200
    pin = client.post("/api/pins", json={"result_id": saved["id"]}).json()
    orders = next(d for d in client.get("/api/datasets").json() if d["name"] == "orders")
    with open(SAMPLE / "orders.csv", "rb") as f:
        client.put(f"/api/datasets/{orders['id']}", files={"file": ("orders.csv", f, "text/csv")})
    client.patch(f"/api/datasets/{orders['id']}", json={"name": "orders_v2"})  # followed
    monkeypatch.setattr("app.agent.provider.get_provider",
                        lambda: pytest.fail("refresh must not call the AI"))

    item = client.post(f"/api/pins/{pin['id']}/refresh").json()
    assert item["status"] == "current"
    assert item["result"]["meta"]["dataset_versions"] == {orders["id"]: 2}
    assert item["result"]["spec"]["base_dataset"] == "orders_v2"
    assert item["result"]["chart"]["type"] == "area"
    assert item["result"]["anomalies"]["status"] == "completed_found"
    assert item["result"]["answer_sentences"][0] == saved["answer_sentences"][0]


def test_refresh_respects_ownership_and_deleted_sources(workspace, make_client):  # noqa: F811
    client, conversation_id = workspace
    saved = ask(client, conversation_id, REGION_Q).json()
    pin = client.post("/api/pins", json={"result_id": saved["id"]}).json()
    other = make_client("someone-else@example.com")
    assert other.post(f"/api/pins/{pin['id']}/refresh").status_code == 404
    orders = next(d for d in client.get("/api/datasets").json() if d["name"] == "orders")
    client.delete(f"/api/datasets/{orders['id']}?confirm=true")
    gone = client.post(f"/api/pins/{pin['id']}/refresh")
    assert gone.status_code == 404 and "deleted" in gone.json()["message"]
