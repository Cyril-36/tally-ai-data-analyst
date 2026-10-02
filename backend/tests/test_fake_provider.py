"""LLM_PROVIDER=fake drives the REAL agent loop, tools and validators with scripted tool calls.
No network and no paid call: the scripted provider never touches the OpenAI client."""

import logging
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.agent import provider
from app.agent.fake_provider import ScriptedProvider
from app.config import get_settings
from app.main import create_app

SAMPLE = Path(__file__).parents[2] / "data" / "sample"
REGION_Q = "Which region generated the highest revenue?"


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()


@pytest.fixture
def workspace(fake, make_client):
    """(client, conversation id) with the sample datasets selected."""
    client = make_client()
    loaded = client.post("/api/datasets/sample").json()["created"]
    created = client.post("/api/conversations", json={
        "title": "Demo", "dataset_ids": [d["id"] for d in loaded]})
    assert created.status_code == 201, created.text
    return client, created.json()["id"]


def ask(client, conversation_id, question, key=None, **params):
    return client.post(f"/api/conversations/{conversation_id}/ask", params=params,
                       json={"question": question, "request_key": key or question})


def orders_revenue() -> pd.DataFrame:
    orders = pd.read_csv(SAMPLE / "orders.csv", parse_dates=["order_date"])
    orders["revenue"] = orders.quantity * orders.unit_price - orders.discount_amount
    return orders


def test_default_provider_is_the_real_one(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    get_settings.cache_clear()
    assert type(provider.get_provider()) is provider.Provider
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    get_settings.cache_clear()
    assert type(provider.get_provider()) is provider.Provider
    monkeypatch.setenv("LLM_PROVIDER", " FAKE ")
    get_settings.cache_clear()
    assert isinstance(provider.get_provider(), ScriptedProvider)
    get_settings.cache_clear()


def test_startup_warns_when_fake_is_active(fake, app, caplog):
    with caplog.at_level(logging.WARNING), TestClient(app):
        pass
    assert any("LLM_PROVIDER=fake" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)


def test_startup_is_quiet_with_the_real_provider(app, caplog, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING), TestClient(create_app()):
        pass
    assert not any("LLM_PROVIDER" in r.getMessage() for r in caplog.records)


def test_highest_revenue_region_matches_pandas(workspace):
    client, conversation_id = workspace
    r = ask(client, conversation_id, REGION_Q)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["outcome"] == "ok"
    expected = (orders_revenue().groupby("region").revenue.sum().sort_values(ascending=False))
    assert [row[0] for row in result["table"]["rows"]] == list(expected.index)
    assert [row[1] for row in result["table"]["rows"]] == pytest.approx(list(expected.values))
    assert expected.index[0] in " ".join(result["answer_sentences"])
    assert [s["tool"] for s in result["trace"] if "tool" in s][::2] == ["run_analysis", "finish"]
    assert result["meta"]["sql"].startswith("SELECT")


@pytest.mark.parametrize("question", ["Show monthly sales trends", "Show monthly revenue"])
def test_monthly_revenue_matches_pandas(workspace, question):
    client, conversation_id = workspace
    result = ask(client, conversation_id, question).json()
    assert result["outcome"] == "ok"
    orders = orders_revenue()
    expected = orders.groupby(orders.order_date.dt.strftime("%Y-%m")).revenue.sum()
    rows = result["table"]["rows"]
    assert [str(row[0])[:7] for row in rows] == list(expected.index)
    assert [row[1] for row in rows] == pytest.approx(list(expected.values))


def test_top_five_customers_joins_and_matches_pandas(workspace):
    client, conversation_id = workspace
    result = ask(client, conversation_id, "What are the top five customers?").json()
    assert result["outcome"] == "ok", result
    customers = pd.read_csv(SAMPLE / "customers.csv")
    merged = orders_revenue().merge(customers, on="customer_id")
    top = merged.groupby(["customer_id", "customer_name"]).revenue.sum().nlargest(5)
    rows = result["table"]["rows"]
    assert len(rows) == 5
    assert [(r[0], r[1]) for r in rows] == list(top.index)
    assert [r[2] for r in rows] == pytest.approx(list(top.values))
    assert [s["tool"] for s in result["trace"] if "tool" in s][::2] == [
        "check_join", "run_analysis", "finish"]


@pytest.mark.parametrize("question", [
    "Detect anomalies in revenue by month", "Are there any anomalies?"])
def test_anomaly_run_detects_the_planted_spike_and_drop(workspace, question):
    client, conversation_id = workspace
    result = ask(client, conversation_id, question).json()
    assert result["outcome"] == "ok", result
    assert result["anomalies"]["status"] == "completed_found"
    sentences = result["answer_sentences"]
    # The answer leads with what was asked for, in readable periods, with little filler.
    assert sentences[0] == "2 unusual values found across 5 series."
    assert sentences[1].startswith("West, Jan 2026: revenue was £18,846.06, unusually high")
    assert sentences[2].startswith("South, Aug 2025: revenue was £183.64, unusually low")
    assert len(sentences) <= 3 + 3 and not any("-01" in s for s in sentences)
    flagged = {(f["row_key"]["region"], str(f["row_key"]["period"])[:7])
               for f in result["anomalies"]["flags"]}
    assert ("West", "2026-01") in flagged and ("South", "2025-08") in flagged
    assert [s["tool"] for s in result["trace"] if "tool" in s][::2] == [
        "run_analysis", "detect_anomalies"]


def test_underperforming_asks_for_a_criterion(workspace):
    client, conversation_id = workspace
    r = ask(client, conversation_id, "Which products are underperforming?")
    assert r.status_code == 200
    result = r.json()
    assert result["outcome"] == "needs_clarification"
    assert result["clarification"]["question"] == "Which metric or criterion should I use?"
    assert result["table"] is None and result["answer_sentences"] == []


def test_anything_else_is_a_clarification_not_an_answer(workspace):
    client, conversation_id = workspace
    result = ask(client, conversation_id, "Tell me a joke about spreadsheets").json()
    assert result["outcome"] == "needs_clarification"
    assert result["table"] is None and result["answer_sentences"] == []


def test_streaming_ask_works_through_the_same_loop(workspace):
    client, conversation_id = workspace
    r = ask(client, conversation_id, REGION_Q, key="stream", stream="true")
    assert r.status_code == 200
    assert "event: tool_call" in r.text and "event: result" in r.text


def test_missing_datasets_are_a_clarification_not_a_failure(fake, make_client):
    client = make_client()
    client.post("/api/datasets", files={"files": ("sales.csv", b"a,b\n1,2\n", "text/csv")})
    dataset = client.get("/api/datasets").json()[0]
    conversation = client.post("/api/conversations", json={
        "title": "x", "dataset_ids": [dataset["id"]]}).json()["id"]
    result = ask(client, conversation, REGION_Q).json()
    assert result["outcome"] == "needs_clarification"
    assert result["clarification"]["options"] == ["sales"]


def test_fake_provider_never_runs_without_the_env_var(make_client, monkeypatch):
    """Without LLM_PROVIDER=fake the real adapter is used (and refuses: no key configured)."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    client = make_client()
    loaded = client.post("/api/datasets/sample").json()["created"]
    conversation = client.post("/api/conversations", json={
        "title": "x", "dataset_ids": [d["id"] for d in loaded]}).json()["id"]
    r = ask(client, conversation, REGION_Q)
    assert r.status_code == 502 and r.json()["outcome"] == "provider_failed"


def test_ready_reports_ai_available_under_the_fake(fake, client):
    assert client.get("/api/ready").json()["ai_configured"] is True


def test_question_outside_the_data_is_declined_as_unsupported(workspace):
    client, conversation_id = workspace
    result = ask(client, conversation_id, "What is our employee attrition rate?").json()
    assert result["outcome"] == "unsupported"
    assert result["table"] is None and result["answer_sentences"] == []


def test_revenue_answer_to_the_clarification_ranks_products_lowest_first(workspace):
    client, conversation_id = workspace
    assert ask(client, conversation_id, "Which products are underperforming?").json()[
        "outcome"] == "needs_clarification"
    result = ask(client, conversation_id, "revenue").json()
    assert result["outcome"] == "ok", result
    products = pd.read_csv(SAMPLE / "products.csv")
    expected = (orders_revenue().merge(products, on="product_id")
                .groupby("product_name").revenue.sum().sort_values().head(5))
    assert [row[0] for row in result["table"]["rows"]] == list(expected.index)
    assert [row[1] for row in result["table"]["rows"]] == pytest.approx(list(expected.values))


def test_stored_anomaly_result_matches_the_returned_one(workspace):
    # Live verification: /ask returned the anomaly sentences but the stored copy (what the UI
    # reloads) still had the pre-detection narrative.
    client, conversation_id = workspace
    returned = ask(client, conversation_id, "Detect anomalies in monthly revenue by region.").json()
    stored = client.get(f"/api/results/{returned['id']}").json()
    stored = stored.get("result", stored)
    assert stored["answer_sentences"] == returned["answer_sentences"]
    assert stored["answer_sentences"][0] == "2 unusual values found across 5 series."
    listed = client.get(f"/api/conversations/{conversation_id}/results").json()
    assert listed[-1]["result"]["answer_sentences"] == returned["answer_sentences"]


def test_currency_comes_from_the_dataset_declaration_even_on_a_cached_result(workspace):
    client, conversation_id = workspace
    datasets = {d["name"]: d for d in client.get("/api/datasets").json()}
    assert datasets["orders"]["currency"] == "GBP"  # the sample generator writes GBP
    first = ask(client, conversation_id, REGION_Q, key="cur-1").json()
    assert first["answer_sentences"][0].startswith("West had the highest revenue: £")

    cleared = client.patch(f"/api/datasets/{datasets['orders']['id']}", json={"currency": None})
    assert cleared.status_code == 200 and cleared.json()["currency"] is None
    second = ask(client, conversation_id, REGION_Q, key="cur-2").json()  # same spec: cache hit
    assert "£" not in " ".join(second["answer_sentences"])
    assert second["answer_sentences"][0].startswith("West had the highest revenue: 1")

    client.patch(f"/api/datasets/{datasets['orders']['id']}", json={"currency": "USD"})
    third = ask(client, conversation_id, REGION_Q, key="cur-3").json()
    assert third["answer_sentences"][0].startswith("West had the highest revenue: $")


def test_dataset_update_rules(workspace, make_client):
    client, _ = workspace
    orders = next(d for d in client.get("/api/datasets").json() if d["name"] == "orders")
    url = f"/api/datasets/{orders['id']}"
    assert client.patch(url, json={}).status_code == 422
    assert client.patch(url, json={"currency": "JPY"}).status_code == 422
    assert client.patch(url, json={"name": None}).status_code == 422
    other = make_client("someone-else@example.com")
    assert other.patch(url, json={"currency": None}).status_code == 404
    with open(SAMPLE / "orders.csv", "rb") as f:
        replaced = client.put(url, files={"file": ("orders.csv", f, "text/csv")})
    assert replaced.status_code == 200 and replaced.json()["currency"] == "GBP"


def test_changing_a_currency_marks_saved_results_and_pins_out_of_date(workspace):
    client, conversation_id = workspace
    orders = next(d for d in client.get("/api/datasets").json() if d["name"] == "orders")
    saved = ask(client, conversation_id, REGION_Q, key="stale-1").json()
    pin = client.post("/api/pins", json={"result_id": saved["id"]}).json()
    status = lambda: client.get(f"/api/results/{saved['id']}").json()["status"]  # noqa: E731
    pin_status = lambda: next(  # noqa: E731
        p["status"] for p in client.get("/api/dashboard").json() if p["id"] == pin["id"])
    assert status() == "current" and pin_status() == "current"

    client.patch(f"/api/datasets/{orders['id']}", json={"name": "orders_renamed"})
    assert status() == "current"  # a rename changes nothing about the figures
    client.patch(f"/api/datasets/{orders['id']}", json={"name": "orders"})

    client.patch(f"/api/datasets/{orders['id']}", json={"currency": "USD"})
    assert status() == "stale" and pin_status() == "stale"
    client.patch(f"/api/datasets/{orders['id']}", json={"currency": "GBP"})
    assert status() == "current"  # back to what the result was calculated with
