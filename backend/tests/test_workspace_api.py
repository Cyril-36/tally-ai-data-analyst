"""Exercise saved analyses over HTTP with real authentication and the isolated executor."""

import json
import time
import uuid

from app.agent.provider import Reply, ToolCall

CSV = b"region,revenue\nWest,10\nEast,20\nWest,5\n"
SPEC = {
    "base_dataset": "sales",
    "metrics": [
        {
            "name": "revenue",
            "agg": "sum",
            "expr": {"kind": "col", "ref": {"dataset": "sales", "column": "revenue"}},
        }
    ],
    "dimensions": [{"dataset": "sales", "column": "region"}],
    "sort": [{"by": "region", "descending": False}],
}


def _setup(client):
    dataset = client.post("/api/datasets", files={"files": ("sales.csv", CSV, "text/csv")})
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()[0]["id"]
    created = client.post(
        "/api/conversations", json={"title": "Sales", "dataset_ids": [dataset_id]}
    )
    assert created.status_code == 201, created.text
    return dataset_id, created.json()["id"]


def _analyze(client, conversation_id, key="first", spec=None):
    return client.post(
        f"/api/conversations/{conversation_id}/analyze",
        json={"question": "Revenue by region", "spec": spec or SPEC, "request_key": key},
    )


def test_analysis_views_exports_replay_and_isolation(make_client):
    owner = make_client("owner@example.com")
    other = make_client("other@example.com")
    admin = make_client("admin@example.com", role="admin")
    dataset_id, conversation_id = _setup(owner)
    response = _analyze(owner, conversation_id)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["table"]["rows"] == [["East", 20.0], ["West", 15.0]]
    assert result["meta"]["dataset_versions"] == {dataset_id: 1}
    assert result["claims"] and result["answer_sentences"]
    assert result["meta"]["sql"].startswith("SELECT")
    assert _analyze(owner, conversation_id).json()["id"] == result["id"]
    conflicting = owner.post(
        f"/api/conversations/{conversation_id}/analyze",
        json={"question": "Different question", "spec": SPEC, "request_key": "first"},
    )
    assert conflicting.status_code == 422

    result_id = result["id"]
    read = owner.get(f"/api/results/{result_id}")
    assert read.json()["status"] == "current"
    chart = owner.get(f"/api/results/{result_id}/chart")
    assert chart.status_code == 200
    assert sorted(chart.json()["data"][0]["y"]) == [15.0, 20.0]
    changed = owner.patch(
        f"/api/results/{result_id}/chart",
        json={"chart": {"type": "pie", "x": "region", "y": ["revenue"], "title": "Share"}},
    )
    assert changed.status_code == 200, changed.text
    assert owner.get(f"/api/results/{result_id}").json()["result"]["chart"]["type"] == "pie"
    csv = owner.get(f"/api/results/{result_id}/export?format=csv")
    assert csv.status_code == 200 and "East,20" in csv.text
    html = owner.get(f"/api/results/{result_id}/export?format=html")
    assert html.status_code == 200 and "Revenue by region" in html.text
    pin = owner.post("/api/pins", json={"result_id": result_id, "idempotency_key": "pin-one"})
    assert pin.status_code == 201, pin.text
    assert len(owner.get("/api/dashboard").json()) == 1

    for client in (other, admin):
        for path in (
            f"/api/conversations/{conversation_id}",
            f"/api/results/{result_id}",
            f"/api/results/{result_id}/chart",
            f"/api/results/{result_id}/export",
        ):
            assert client.get(path).status_code == 404
        assert client.post("/api/pins", json={"result_id": result_id}).status_code == 404
        assert client.get("/api/dashboard").json() == []


def test_conversation_context_changes_and_live_turns_are_guarded(make_client, db):
    owner = make_client()
    dataset_id, conversation_id = _setup(owner)
    rejected = owner.patch(
        f"/api/conversations/{conversation_id}", json={"state": {"last_spec": SPEC}}
    )
    assert rejected.status_code == 422
    assert _analyze(owner, conversation_id).status_code == 200
    state = owner.get(f"/api/conversations/{conversation_id}").json()["state"]
    assert state["last_spec"]["base_dataset"] == SPEC["base_dataset"]
    assert state["last_spec"]["metrics"] == SPEC["metrics"]

    another = owner.post("/api/datasets", files={"files": ("sales2.csv", CSV, "text/csv")}).json()[
        0
    ]["id"]
    changed = owner.patch(f"/api/conversations/{conversation_id}", json={"dataset_ids": [another]})
    assert changed.status_code == 200
    assert changed.json()["state"] == {"dataset_ids": [another]}
    old = _analyze(owner, conversation_id, "old", SPEC)
    assert old.status_code == 422  # the prior dataset is no longer selected

    active_id = uuid.uuid4().hex
    db.execute(
        "INSERT INTO turns VALUES (?, ?, 'busy', '{}', NULL, 'running', ?)",
        (active_id, conversation_id, time.time()),
    )
    assert (
        owner.patch(
            f"/api/conversations/{conversation_id}", json={"dataset_ids": [dataset_id]}
        ).status_code
        == 422
    )
    assert owner.delete(f"/api/conversations/{conversation_id}").status_code == 422


def test_agent_route_uses_tools_then_finishes_without_model_authored_numbers(
    make_client, monkeypatch
):
    owner = make_client()
    _, conversation_id = _setup(owner)

    class FakeProvider:
        def complete(self, messages, remaining_s, max_tokens):
            observations = [
                json.loads(message["content"]) for message in messages if message["role"] == "tool"
            ]
            if not observations:
                call = ToolCall("a", "run_analysis", {"spec_json": json.dumps(SPEC)})
            else:
                call = ToolCall("b", "finish", {"result_id": observations[-1]["result_id"]})
            return Reply([call], tokens=100)

    monkeypatch.setattr("app.agent.provider.get_provider", lambda: FakeProvider())
    response = owner.post(
        f"/api/conversations/{conversation_id}/ask",
        json={"question": "Revenue by region", "request_key": "fake-provider"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["outcome"] == "ok"
    assert result["table"]["rows"] == [["East", 20.0], ["West", 15.0]]
    assert "East" in " ".join(result["answer_sentences"])
    assert [step["tool"] for step in result["trace"] if "tool" in step][::2] == [
        "run_analysis",
        "finish",
    ]


def test_agent_clarification_streams_only_verified_terminal_result(make_client, monkeypatch):
    owner = make_client()
    _, conversation_id = _setup(owner)

    class ClarifyingProvider:
        def complete(self, messages, remaining_s, max_tokens):
            return Reply(
                [ToolCall("clarify", "clarify", {"reason": "metric", "options": ["revenue"]})],
                tokens=50,
            )

    monkeypatch.setattr("app.agent.provider.get_provider", lambda: ClarifyingProvider())
    response = owner.post(
        f"/api/conversations/{conversation_id}/ask?stream=true",
        json={"question": "Which region is best?", "request_key": "clarification"},
    )
    assert response.status_code == 200
    assert "event: planning" in response.text
    assert "event: tool_call" in response.text
    terminal = json.loads(response.text.split("event: result\ndata: ")[1].split("\n\n")[0])
    assert terminal["outcome"] == "needs_clarification"
    assert terminal["clarification"]["question"] == "Which metric or criterion should I use?"
    assert terminal["answer_sentences"] == []


def test_provider_failure_releases_turn_and_same_key_can_retry(make_client, monkeypatch, db):
    owner = make_client()
    _, conversation_id = _setup(owner)

    class BrokenProvider:
        def complete(self, messages, remaining_s, max_tokens):
            from app.contracts.outcomes import AppError, Outcome

            raise AppError(Outcome.provider_failed, "Provider unavailable")

    monkeypatch.setattr("app.agent.provider.get_provider", lambda: BrokenProvider())
    failed = owner.post(
        f"/api/conversations/{conversation_id}/ask",
        json={"question": "Revenue by region", "request_key": "retry"},
    )
    assert failed.status_code == 502
    turn = db.execute(
        "SELECT status FROM turns WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    assert turn["status"] == "failed"

    class EndingProvider:
        def complete(self, messages, remaining_s, max_tokens):
            return Reply([ToolCall("end", "unsupported", {"reason": "operation"})], tokens=50)

    monkeypatch.setattr("app.agent.provider.get_provider", lambda: EndingProvider())
    retried = owner.post(
        f"/api/conversations/{conversation_id}/ask",
        json={"question": "Revenue by region", "request_key": "retry"},
    )
    assert retried.status_code == 200 and retried.json()["outcome"] == "unsupported"


def test_ai_token_budget_is_not_reported_as_provider_outage(make_client, monkeypatch):
    from app.config import get_settings

    owner = make_client()
    _, conversation_id = _setup(owner)
    monkeypatch.setenv("LLM_TURN_TOKEN_CAP", "300")
    get_settings.cache_clear()

    class ExpensiveProvider:
        def complete(self, messages, remaining_s, max_tokens):
            assert max_tokens == 300
            return Reply([ToolCall("end", "unsupported", {"reason": "operation"})], tokens=301)

    monkeypatch.setattr("app.agent.provider.get_provider", lambda: ExpensiveProvider())
    response = owner.post(
        f"/api/conversations/{conversation_id}/ask",
        json={"question": "Complex analysis", "request_key": "budget"},
    )
    assert response.status_code == 422
    assert response.json()["outcome"] == "budget_exhausted"
    assert "token budget" in response.json()["message"]


def test_agent_stops_repeating_successful_analyses(make_client, monkeypatch):
    owner = make_client()
    _, conversation_id = _setup(owner)

    class RepeatingProvider:
        def complete(self, messages, remaining_s, max_tokens):
            observations = [
                json.loads(message["content"])
                for message in messages
                if message["role"] == "tool"
            ]
            if len(observations) < 3:
                call = ToolCall(
                    f"a-{len(observations)}", "run_analysis", {"spec_json": json.dumps(SPEC)}
                )
            else:
                assert observations[-1]["outcome"] == "invalid_input"
                call = ToolCall("finish", "finish", {"result_id": observations[-2]["result_id"]})
            return Reply([call], tokens=100)

    monkeypatch.setattr("app.agent.provider.get_provider", lambda: RepeatingProvider())
    response = owner.post(
        f"/api/conversations/{conversation_id}/ask",
        json={"question": "Revenue by region", "request_key": "repeated"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "ok"
    assert response.json()["table"]["rows"] == [["East", 20.0], ["West", 15.0]]


def test_explicit_metric_question_recovers_from_needless_metric_clarification(
    make_client, monkeypatch
):
    owner = make_client()
    uploaded = owner.post(
        "/api/datasets",
        files={
            "files": ("support.csv", b"team,resolution_minutes\nAtlas,20\nBeacon,30\n", "text/csv")
        },
    )
    assert uploaded.status_code == 201
    created = owner.post(
        "/api/conversations",
        json={"title": "Support", "dataset_ids": [uploaded.json()[0]["id"]]},
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    spec = {
        "base_dataset": "support",
        "dimensions": [{"dataset": "support", "column": "team"}],
        "metrics": [{"name": "minutes", "agg": "sum", "expr": {
            "kind": "col", "ref": {"dataset": "support", "column": "resolution_minutes"}
        }}],
    }

    class ClarifyThenAnalyze:
        def complete(self, messages, remaining_s, max_tokens):
            observations = [
                json.loads(message["content"])
                for message in messages
                if message["role"] == "tool"
            ]
            if not observations:
                call = ToolCall("clarify", "clarify", {"reason": "metric"})
            elif observations[-1].get("outcome") == "invalid_input":
                call = ToolCall("analyze", "run_analysis", {"spec_json": json.dumps(spec)})
            else:
                call = ToolCall("finish", "finish", {"result_id": observations[-1]["result_id"]})
            return Reply([call], tokens=100)

    monkeypatch.setattr("app.agent.provider.get_provider", lambda: ClarifyThenAnalyze())
    response = owner.post(
        f"/api/conversations/{conversation_id}/ask",
        json={"question": "Show total resolution_minutes by team",
              "request_key": "explicit-metric"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "ok"


def test_single_metric_anomaly_tool_accepts_generic_metric_name(make_client, monkeypatch):
    owner = make_client()
    _, conversation_id = _setup(owner)
    aliased = {**SPEC, "metrics": [{**SPEC["metrics"][0], "name": "gross_sales"}]}

    class GenericMetricProvider:
        def complete(self, messages, remaining_s, max_tokens):
            observations = [
                json.loads(message["content"])
                for message in messages
                if message["role"] == "tool"
            ]
            if not observations:
                call = ToolCall("analyze", "run_analysis", {"spec_json": json.dumps(aliased)})
            else:
                call = ToolCall(
                    "detect", "detect_anomalies",
                    {"result_id": observations[-1]["result_id"], "metric": "revenue"},
                )
            return Reply([call], tokens=100)

    monkeypatch.setattr("app.agent.provider.get_provider", lambda: GenericMetricProvider())
    response = owner.post(
        f"/api/conversations/{conversation_id}/ask",
        json={"question": "Check unusual revenue", "request_key": "metric-alias"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["outcome"] == "ok"
    assert result["anomalies"]["status"] == "insufficient_data"
    assert result["answer_sentences"][0].startswith("Unusual values could not be checked:")


def test_clarification_options_are_plain_phrases_for_any_schema():
    from app.agent.service import _plain_option

    for ok in ("revenue", "resolution_time", "declining sales", "tickets per agent"):
        assert _plain_option(ok), ok
    for bad in ("revenue was £5,000", "top 5", "<b>x</b>", "", "a b c d e f g", 42):
        assert not _plain_option(bad), bad


def test_a_new_pin_is_titled_with_its_question(make_client, monkeypatch):
    from tests.test_fake_provider import ask

    monkeypatch.setenv("LLM_PROVIDER", "fake")
    from app.config import get_settings

    get_settings.cache_clear()
    client = make_client()
    loaded = client.post("/api/datasets/sample").json()["created"]
    conv = client.post("/api/conversations", json={
        "title": "t", "dataset_ids": [d["id"] for d in loaded]}).json()["id"]
    question = "Which region generated the highest revenue?"
    result = ask(client, conv, question).json()
    pin = client.post("/api/pins", json={"result_id": result["id"]}).json()
    assert pin["title"] == question
    get_settings.cache_clear()
