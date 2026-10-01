import json

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
    assert admin.get("/api/admin/settings").json() == {"max_upload_mb": 50, "llm_enabled": True}
    r = admin.put("/api/admin/settings", json={"max_upload_mb": 120, "llm_enabled": False})
    assert r.status_code == 200
    assert admin.get("/api/admin/settings").json() == {"max_upload_mb": 120, "llm_enabled": False}
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
