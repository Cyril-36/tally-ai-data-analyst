import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth import authz
from app.auth.deps import User
from app.config import get_settings
from app.contracts.outcomes import AppError, Outcome
from tests.conftest import PASSWORD


def test_analyst_cannot_read_or_change_settings(make_client):
    c = make_client()
    for r in (c.get("/api/admin/settings"),
              c.put("/api/admin/settings", json={"max_upload_mb": 10, "llm_enabled": False})):
        assert r.status_code == 403
        assert r.json() == {"outcome": "permission_denied",
                            "message": "Only an admin can change system settings."}


def test_settings_require_sign_in(client):
    assert client.get("/api/admin/settings").status_code == 401


def test_admin_can_change_settings_and_they_persist(make_client):
    admin = make_client("admin@example.com", role="admin")
    assert admin.get("/api/admin/settings").json() == {
        "max_upload_mb": 50, "llm_enabled": True, "sample_data_enabled": True,
        "retention_days": 90}
    r = admin.put("/api/admin/settings", json={"max_upload_mb": 120, "llm_enabled": False})
    assert r.status_code == 200
    assert admin.get("/api/admin/settings").json() == {
        "max_upload_mb": 120, "llm_enabled": False, "sample_data_enabled": True,
        "retention_days": 90}
    admin.put("/api/admin/settings", json={"max_upload_mb": 120, "llm_enabled": True})
    assert admin.get("/api/admin/settings").json()["llm_enabled"] is True


@pytest.mark.parametrize("bad", [
    {"max_upload_mb": 0, "llm_enabled": True},
    {"max_upload_mb": 201, "llm_enabled": True},
    {"max_upload_mb": 10, "llm_enabled": "maybe"},
    {"max_upload_mb": 10},
])
def test_settings_validation(make_client, bad):
    admin = make_client("admin@example.com", role="admin")
    r = admin.put("/api/admin/settings", json=bad)
    assert r.status_code == 422
    assert r.json()["outcome"] == "invalid_input"


def test_admin_gets_no_access_to_other_users_data(make_client, db):
    analyst = make_client("analyst@example.com")
    admin = make_client("admin@example.com", role="admin")
    owner_id = analyst.get("/api/auth/me").json()["id"]
    admin_me = admin.get("/api/auth/me").json()
    db.execute("INSERT INTO conversations (id, owner_id, title, created_at) "
               "VALUES ('c1', ?, 'private chat', '2026-01-01')", (owner_id,))

    owner = User(id=owner_id, email="analyst@example.com", role="analyst")
    assert authz.require_owned(db, "conversations", "c1", owner, authz.Action.read)["id"] == "c1"

    admin_user = User(id=admin_me["id"], email=admin_me["email"], role="admin")
    with pytest.raises(AppError) as exc:
        authz.require_owned(db, "conversations", "c1", admin_user, authz.Action.read)
    assert exc.value.outcome is Outcome.not_found


def test_admin_is_seeded_from_env_on_startup(app, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "boss@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)
    get_settings.cache_clear()
    headers = {"X-Requested-With": "test"}
    with TestClient(app, headers=headers) as c:
        r = c.post("/api/auth/login", json={"email": "boss@example.com", "password": PASSWORD})
        assert r.json()["role"] == "admin"
    with TestClient(app, headers=headers):  # restarting does not duplicate or fail
        pass


def test_no_api_path_creates_an_admin(client):
    r = client.post("/api/auth/signup", headers={"X-Requested-With": "test"},
                    content=json.dumps({"email": "x@example.com", "password": PASSWORD,
                                        "role": "admin"}))
    assert r.status_code in (201, 422)
    assert client.get("/api/auth/me").json().get("role", "analyst") == "analyst"


def test_analyst_refused_before_body_validation(make_client):
    r = make_client().put("/api/admin/settings", json={})
    assert r.status_code == 403
    assert r.json()["message"] == "Only an admin can change system settings."


# ---- sample_data_enabled / retention_days --------------------------------------------

LEGACY = {"max_upload_mb": 10, "llm_enabled": True}


def test_new_settings_are_stored_and_old_clients_keep_them(make_client):
    admin = make_client("admin@example.com", role="admin")
    r = admin.put("/api/admin/settings", json={**LEGACY, "sample_data_enabled": False,
                                               "retention_days": 365})
    assert r.status_code == 200
    assert r.json() == {**LEGACY, "sample_data_enabled": False, "retention_days": 365,
                        "purged_conversations": 0}
    # a PUT without the new fields keeps the stored values
    assert admin.put("/api/admin/settings", json={**LEGACY, "max_upload_mb": 20}).status_code == 200
    assert admin.get("/api/admin/settings").json() == {
        "max_upload_mb": 20, "llm_enabled": True, "sample_data_enabled": False,
        "retention_days": 365}
    # null is explicit: keep forever, and it is remembered
    assert admin.put("/api/admin/settings",
                     json={**LEGACY, "retention_days": None}).json()["retention_days"] is None
    admin.put("/api/admin/settings", json=LEGACY)
    assert admin.get("/api/admin/settings").json()["retention_days"] is None
    thirty = admin.put("/api/admin/settings", json={**LEGACY, "retention_days": 30})
    assert thirty.status_code == 200
    assert admin.get("/api/admin/settings").json()["retention_days"] == 30


@pytest.mark.parametrize("bad", [
    {"retention_days": 60}, {"retention_days": 0}, {"retention_days": "90"},
    {"retention_days": "never"}, {"sample_data_enabled": "yes"},
])
def test_new_settings_validation(make_client, bad):
    admin = make_client("admin@example.com", role="admin")
    r = admin.put("/api/admin/settings", json={**LEGACY, **bad})
    assert r.status_code == 422 and r.json()["outcome"] == "invalid_input"
    assert admin.get("/api/admin/settings").json()["retention_days"] == 90  # nothing changed


def test_analyst_is_refused_the_new_settings_and_provider_test(make_client):
    c = make_client()
    refused = [
        c.put("/api/admin/settings", json={**LEGACY, "sample_data_enabled": False,
                                           "retention_days": 30}),
        c.put("/api/admin/settings", json={"retention_days": 7}),  # refused before validation
        c.post("/api/admin/provider-test"),
    ]
    for r in refused:
        assert r.status_code == 403
        assert r.json() == {"outcome": "permission_denied",
                            "message": "Only an admin can change system settings."}
    admin_view = make_client("admin@example.com", role="admin").get("/api/admin/settings")
    assert admin_view.json()["retention_days"] == 90 and admin_view.json()["sample_data_enabled"]


def test_provider_test_requires_sign_in(client):
    assert client.post("/api/admin/provider-test", headers={"X-Requested-With": "t"}
                       ).status_code == 401


# ---- provider test -------------------------------------------------------------------


class _Pinger:
    def __init__(self, error=None):
        self.error, self.calls = error, 0

    def ping(self):
        self.calls += 1
        if self.error:
            raise self.error
        return "test-model"


def test_provider_test_reports_latency_and_model(make_client, monkeypatch):
    pinger = _Pinger()
    monkeypatch.setattr("app.agent.provider.get_provider", lambda: pinger)
    r = make_client("admin@example.com", role="admin").post("/api/admin/provider-test")
    assert r.status_code == 200 and pinger.calls == 1
    body = r.json()
    assert body["ok"] is True and body["model"] == "test-model"
    assert isinstance(body["latency_ms"], int) and body["latency_ms"] >= 0


def test_provider_test_failure_never_echoes_provider_text(make_client, monkeypatch):
    leak = "sk-secret-123 https://internal.example/v1 quota exceeded"
    monkeypatch.setattr("app.agent.provider.get_provider",
                        lambda: _Pinger(AppError(Outcome.provider_failed, leak)))
    r = make_client("admin@example.com", role="admin").post("/api/admin/provider-test")
    assert r.status_code == 502
    assert r.json() == {"outcome": "provider_failed",
                        "message": "The AI provider did not respond correctly."}


def test_provider_test_is_refused_while_ai_is_off_and_makes_no_call(make_client, monkeypatch):
    pinger = _Pinger()
    monkeypatch.setattr("app.agent.provider.get_provider", lambda: pinger)
    admin = make_client("admin@example.com", role="admin")
    admin.put("/api/admin/settings", json={**LEGACY, "llm_enabled": False})
    r = admin.post("/api/admin/provider-test")
    assert r.status_code == 409
    assert r.json() == {"outcome": "invalid_input",
                        "message": "AI is turned off; turn it on to test the provider."}
    assert pinger.calls == 0


class _FakeOpenAI:
    """Stands in for the OpenAI client so no paid request is made."""

    created: list[dict] = []
    requests: list[dict] = []
    error: Exception | None = None

    def __init__(self, **kwargs):
        self.created.append(kwargs)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace()])


@pytest.fixture
def fake_openai(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key-value")
    monkeypatch.setenv("LLM_MODEL", "demo-model")
    get_settings.cache_clear()
    _FakeOpenAI.created, _FakeOpenAI.requests, _FakeOpenAI.error = [], [], None
    monkeypatch.setattr("app.agent.provider.OpenAI", _FakeOpenAI)
    return _FakeOpenAI


def test_real_provider_ping_is_one_tiny_request(make_client, fake_openai):
    r = make_client("admin@example.com", role="admin").post("/api/admin/provider-test")
    assert r.status_code == 200 and r.json()["model"] == "demo-model"
    assert len(fake_openai.requests) == 1
    request = fake_openai.requests[0]
    assert request["max_tokens"] <= 16 and "tools" not in request
    assert fake_openai.created[0]["timeout"] <= 10 and fake_openai.created[0]["max_retries"] == 0


def test_real_provider_ping_hides_exception_text_and_keys(make_client, fake_openai, caplog):
    fake_openai.error = RuntimeError("401 Incorrect API key sk-test-key-value at https://x.test")
    r = make_client("admin@example.com", role="admin").post("/api/admin/provider-test")
    assert r.status_code == 502
    assert r.json()["message"] == "The AI provider did not respond correctly."
    assert "sk-" not in r.text and "x.test" not in r.text
    assert "sk-test-key-value" not in caplog.text  # nor in the logs


def test_real_provider_ping_without_a_key_is_the_same_failure(make_client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    r = make_client("admin@example.com", role="admin").post("/api/admin/provider-test")
    assert r.status_code == 502
    assert r.json()["message"] == "The AI provider did not respond correctly."


# ---- retention -----------------------------------------------------------------------


def _ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _conversation(db, owner_id, cid, created_days, result_days=None, turn_days=None, pin=False):
    """A conversation created ``created_days`` ago, optionally with a result, turn and pin."""
    db.execute("INSERT INTO conversations (id, owner_id, title, created_at) VALUES (?, ?, ?, ?)",
               (cid, owner_id, cid, _ago(created_days)))
    if result_days is not None:
        db.execute("INSERT INTO results VALUES (?, ?, ?, '{}', '{}', ?)",
                   (f"r-{cid}", owner_id, cid, _ago(result_days)))
        if pin:
            db.execute("INSERT INTO pins (id, owner_id, result_id, created_at)"
                       " VALUES (?, ?, ?, '2020-01-01')", (f"p-{cid}", owner_id, f"r-{cid}"))
    if turn_days is not None:
        db.execute("INSERT INTO turns VALUES (?, ?, 'k', '{}', NULL, 'done', ?)",
                   (f"t-{cid}", cid, time.time() - turn_days * 86400))


def _ids(db, table):
    return {r["id"] for r in db.execute(f"SELECT id FROM {table}")}  # noqa: S608


@pytest.fixture
def aged(make_client, db):
    admin = make_client("admin@example.com", role="admin")
    analyst = make_client("analyst@example.com")
    owner = analyst.get("/api/auth/me").json()["id"]
    dataset = analyst.post("/api/datasets", files={"files": ("s.csv", b"a\n1\n", "text/csv")})
    _conversation(db, owner, "stale", 200, result_days=199, turn_days=198, pin=True)
    _conversation(db, owner, "stale-bare", 120)
    _conversation(db, owner, "old-but-new-result", 200, result_days=5, pin=True)
    _conversation(db, owner, "old-but-new-turn", 200, turn_days=2)
    _conversation(db, owner, "mid", 60, result_days=60, pin=True)
    _conversation(db, owner, "fresh", 1)
    return admin, owner, dataset.json()[0]["id"]


def test_saving_retention_purges_only_idle_conversations_and_cascades(aged, db):
    admin, _, dataset_id = aged
    r = admin.put("/api/admin/settings", json={**LEGACY, "retention_days": 90})
    assert r.json()["purged_conversations"] == 2
    assert _ids(db, "conversations") == {"old-but-new-result", "old-but-new-turn", "mid",
                                         "fresh"}
    assert _ids(db, "results") == {"r-old-but-new-result", "r-mid"}
    assert _ids(db, "pins") == {"p-old-but-new-result", "p-mid"}  # the stale pin went with it
    assert _ids(db, "turns") == {"t-old-but-new-turn"}
    assert _ids(db, "datasets") == {dataset_id}  # datasets are never purged

    r = admin.put("/api/admin/settings", json={**LEGACY, "retention_days": 30})
    assert r.json()["purged_conversations"] == 1  # "mid" (60 days idle) now qualifies
    assert _ids(db, "pins") == {"p-old-but-new-result"}
    again = admin.put("/api/admin/settings", json={**LEGACY, "retention_days": 30})
    assert again.json()["purged_conversations"] == 0


def test_never_keeps_everything(aged, db):
    admin, *_ = aged
    r = admin.put("/api/admin/settings", json={**LEGACY, "retention_days": None})
    assert r.json()["purged_conversations"] == 0
    assert len(_ids(db, "conversations")) == 6 and len(_ids(db, "pins")) == 3


def test_purge_runs_at_startup_using_the_stored_setting(aged, db, app):
    admin, *_ = aged
    admin.put("/api/admin/settings", json={**LEGACY, "retention_days": None})
    with TestClient(app):
        pass
    assert len(_ids(db, "conversations")) == 6  # never: startup purges nothing

    db.execute("DELETE FROM settings WHERE key = 'retention_days'")  # back to the 90-day default
    with TestClient(app):
        pass
    assert _ids(db, "conversations") == {"old-but-new-result", "old-but-new-turn", "mid", "fresh"}
    assert _ids(db, "pins") == {"p-old-but-new-result", "p-mid"}


def test_retention_is_system_wide_and_follows_the_chosen_window(aged, db):
    # Retention is system-wide: it applies to every user's idle conversations equally.
    admin, owner, _ = aged
    other = db.execute("SELECT id FROM users WHERE email = 'admin@example.com'").fetchone()["id"]
    _conversation(db, other, "admin-stale", 400)
    _conversation(db, other, "admin-fresh", 3)
    admin.put("/api/admin/settings", json={**LEGACY, "retention_days": 365})
    assert {"admin-stale"}.isdisjoint(_ids(db, "conversations"))
    assert {"admin-fresh", "stale"} <= _ids(db, "conversations")  # 200 days < 365
