import pytest
from fastapi.testclient import TestClient

from app.auth import passwords
from app.config import get_settings
from app.db import connect, db_path, init_db
from app.main import create_app

PASSWORD = "correct-horse-battery"


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A fresh app on an empty temp DATA_DIR."""
    for name in ("ADMIN_EMAIL", "ADMIN_PASSWORD", "DEMO_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    init_db(db_path())
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app):
    """Anonymous client with no default headers (so CSRF behaviour is visible)."""
    return TestClient(app)


@pytest.fixture
def db(app):
    with connect(db_path()) as conn:
        yield conn


@pytest.fixture
def make_client(app, db):
    """make_client(email, role="analyst") creates the user if needed, logs in over the API
    and returns a client holding the session cookie and the X-Requested-With header."""

    def make(email: str = "analyst@example.com", role: str = "analyst", password: str = PASSWORD):
        if not passwords.user_exists(db, email):
            passwords.create_user(db, email, password, role)
        c = TestClient(app, headers={"X-Requested-With": "test"})
        r = c.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return c

    return make
