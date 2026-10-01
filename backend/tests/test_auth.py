import logging
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.logging import JsonFormatter
from tests.conftest import PASSWORD

CSRF = {"X-Requested-With": "test"}


def _ago(**kwargs) -> str:
    return (datetime.now(UTC) - timedelta(**kwargs)).isoformat()


def _signup(client, email="new@example.com", **extra):
    return client.post("/api/auth/signup", headers=CSRF,
                       json={"email": email, "password": PASSWORD, **extra})


def test_signup_login_me_logout_flow(client):
    r = _signup(client)
    assert r.status_code == 201
    assert r.json()["role"] == "analyst"
    assert client.get("/api/auth/me").json()["email"] == "new@example.com"  # signup signs in

    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert client.get("/api/auth/me").status_code == 401

    r = client.post("/api/auth/login", headers=CSRF,
                    json={"email": "NEW@example.com", "password": PASSWORD})
    assert r.status_code == 200
    assert client.get("/api/auth/me").json()["email"] == "new@example.com"


def test_many_authenticated_requests_work(make_client):
    c = make_client()
    assert all(c.get("/api/auth/me").status_code == 200 for _ in range(30))


def test_signup_cannot_choose_admin_role(client):
    _signup(client, role="admin", is_admin=True)
    assert client.get("/api/auth/me").json()["role"] == "analyst"


def test_signup_validation(client):
    short = client.post("/api/auth/signup", headers=CSRF,
                        json={"email": "a@example.com", "password": "short"})
    assert short.status_code == 422
    assert short.json()["outcome"] == "invalid_input"
    assert "short" not in short.json()["message"]
    bad_email = client.post("/api/auth/signup", headers=CSRF,
                            json={"email": "nope", "password": PASSWORD})
    assert bad_email.status_code == 422
    assert _signup(client).status_code == 201
    assert _signup(client).status_code == 422  # duplicate email


def test_password_is_stored_as_argon2id(client, db):
    _signup(client)
    stored = db.execute("SELECT password_hash FROM users").fetchone()[0]
    assert stored.startswith("$argon2id$")


def test_wrong_password_and_unknown_email_are_indistinguishable(client):
    _signup(client)
    client.cookies.clear()
    wrong = client.post("/api/auth/login", headers=CSRF,
                        json={"email": "new@example.com", "password": "wrong-password-1"})
    unknown = client.post("/api/auth/login", headers=CSRF,
                          json={"email": "ghost@example.com", "password": "wrong-password-1"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["message"] == "Invalid email or password."
    assert "sid" not in client.cookies


def test_login_rate_limit_per_ip_and_email(client):
    body = {"email": "new@example.com", "password": "wrong-password-1"}
    codes = [client.post("/api/auth/login", headers=CSRF, json=body).status_code
             for _ in range(6)]
    assert codes == [401] * 5 + [429]
    other = client.post("/api/auth/login", headers=CSRF,
                        json={**body, "email": "other@example.com"})
    assert other.status_code == 401  # a different email has its own budget


def test_me_requires_sign_in(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json() == {"outcome": "permission_denied", "message": "Please sign in."}


def test_session_cookie_flags(client, app):
    r = _signup(client)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie
    https = TestClient(app, base_url="https://testserver")
    r = https.post("/api/auth/signup", headers=CSRF,
                   json={"email": "tls@example.com", "password": PASSWORD})
    assert "secure" in r.headers["set-cookie"].lower()


def test_idle_expiry_and_activity_refresh(make_client, db):
    c = make_client()
    db.execute("UPDATE sessions SET last_seen_at = ?", (_ago(hours=7),))
    assert c.get("/api/auth/me").status_code == 200  # still inside 8h, and refreshes it
    refreshed = db.execute("SELECT last_seen_at FROM sessions").fetchone()[0]
    assert refreshed > _ago(minutes=1)

    db.execute("UPDATE sessions SET last_seen_at = ?", (_ago(hours=9),))
    assert c.get("/api/auth/me").status_code == 401
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_absolute_expiry_even_when_active(make_client, db):
    c = make_client()
    db.execute("UPDATE sessions SET created_at = ?", (_ago(hours=25),))
    assert c.get("/api/auth/me").status_code == 401


def test_logout_invalidates_the_session_server_side(make_client, app):
    c = make_client()
    sid = c.cookies["sid"]
    c.post("/api/auth/logout")
    stolen = TestClient(app, cookies={"sid": sid})
    assert stolen.get("/api/auth/me").status_code == 401


def test_password_change_kills_other_sessions(make_client):
    laptop, phone = make_client(), make_client()
    new_password = "a-brand-new-password"
    r = laptop.post("/api/auth/change-password",
                    json={"current_password": PASSWORD, "new_password": new_password})
    assert r.status_code == 204
    assert phone.get("/api/auth/me").status_code == 401
    assert laptop.get("/api/auth/me").status_code == 200  # the changing browser stays in
    login = laptop.post("/api/auth/login",
                        json={"email": "analyst@example.com", "password": PASSWORD})
    assert login.status_code == 401
    login = laptop.post("/api/auth/login",
                        json={"email": "analyst@example.com", "password": new_password})
    assert login.status_code == 200


def test_password_change_needs_current_password(make_client):
    c = make_client()
    r = c.post("/api/auth/change-password",
               json={"current_password": "not-my-password", "new_password": "a-brand-new-password"})
    assert r.status_code == 422
    assert c.get("/api/auth/me").status_code == 200


def test_csrf_header_required_on_state_changing_requests(client):
    body = {"email": "new@example.com", "password": PASSWORD}
    for method in ("post", "put", "patch", "delete"):
        r = client.request(method.upper(), "/api/auth/signup", json=body)
        assert r.status_code == 403, method
        assert r.json()["outcome"] == "permission_denied"
    assert client.get("/api/health").status_code == 200  # reads need no header
    assert client.post("/api/auth/signup", json=body, headers=CSRF).status_code == 201


def test_unhandled_exception_returns_no_traceback(app, caplog):
    def boom():
        raise RuntimeError("secret internal detail")

    app.add_api_route("/api/boom", boom)
    with caplog.at_level(logging.INFO, logger="app"):
        r = TestClient(app).get("/api/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["outcome"] == "execution_failed"
    assert body["message"] == f"Something went wrong (request {r.headers['X-Request-ID']})."
    assert "secret internal detail" not in r.text and "Traceback" not in r.text
    assert any(rec.exc_info for rec in caplog.records)  # but the traceback was logged


def test_logs_are_structured_and_contain_no_secrets(client, caplog):
    secret = "hunter2-very-secret-password"
    with caplog.at_level(logging.INFO, logger="app"):
        client.post("/api/auth/signup", headers=CSRF,
                    json={"email": "log@example.com", "password": secret})
        client.post("/api/auth/login", headers=CSRF,
                    json={"email": "log@example.com", "password": secret + "x"})
        client.get("/api/auth/me")
    formatter = JsonFormatter()
    lines = [formatter.format(r) for r in caplog.records if r.name == "app.request"]
    assert len(lines) == 3
    for line in lines:
        assert secret not in line
        assert "sid" not in line and "Cookie" not in line
    me_line = lines[-1]
    for field in ("request_id", "method", "path", "status", "latency_ms", "user_id"):
        assert f'"{field}"' in me_line
    assert secret not in caplog.text
