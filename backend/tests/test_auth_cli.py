import getpass

from app import cli
from app.db import connect, db_path


def _roles() -> dict[str, str]:
    with connect(db_path()) as conn:
        return {r["email"]: r["role"] for r in conn.execute("SELECT email, role FROM users")}


def test_seed_demo_refuses_without_demo_password(app, capsys):
    assert cli.main(["seed-demo"]) == 1
    assert "DEMO_PASSWORD" in capsys.readouterr().err
    assert _roles() == {}


def test_seed_demo_creates_the_three_accounts_once(app, monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", "demo-only-password")
    assert cli.main(["seed-demo"]) == 0
    assert cli.main(["seed-demo"]) == 0  # idempotent
    assert _roles() == {"analyst1@example.com": "analyst", "analyst2@example.com": "analyst",
                        "admin@example.com": "admin"}


def test_create_admin_prompts_for_password(app, monkeypatch):
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "an-admin-password")
    assert cli.main(["create-admin", "--email", "Root@Example.com"]) == 0
    assert _roles() == {"root@example.com": "admin"}


def test_create_admin_rejects_mismatch_and_short_passwords(app, monkeypatch):
    answers = iter(["an-admin-password", "different-password"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert cli.main(["create-admin", "--email", "root@example.com"]) == 1
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "short")
    assert cli.main(["create-admin", "--email", "root@example.com"]) == 1
    assert _roles() == {}
