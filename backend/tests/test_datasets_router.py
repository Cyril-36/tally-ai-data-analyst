"""Dataset HTTP endpoints, mounted on a bare FastAPI app with auth and the DB overridden."""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.auth.deps import User, get_current_user
from app.config import get_settings
from app.contracts.outcomes import HTTP_STATUS, AppError, ErrorBody
from app.datasets.router import router
from app.db import dataset_parquet_path, get_conn, init_db

FIXTURES = Path(__file__).parent / "fixtures"
ALICE = User(id="u-alice", email="alice@example.com", role="analyst")
BOB = User(id="u-bob", email="bob@example.com", role="analyst")
CSV = b"region,revenue\nNorth,10.5\nSouth,20\nWest,5\n"


class Env:
    def __init__(self, client: TestClient, conn: sqlite3.Connection, state: dict):
        self.client, self.conn, self._state = client, conn, state

    def as_user(self, user: User) -> "Env":
        self._state["user"] = user
        return self

    def upload(self, name: str = "sales.csv", data: bytes = CSV) -> dict:
        r = self.client.post("/api/datasets", files=[("files", (name, data, "text/csv"))])
        assert r.status_code == 201, r.text
        return r.json()[0]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    db_file = tmp_path / "app.sqlite3"
    init_db(db_file)
    conn = sqlite3.connect(db_file, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    for u in (ALICE, BOB):
        conn.execute("INSERT INTO users VALUES (?, ?, 'x', 'analyst', '2026-01-01')",
                     (u.id, u.email))

    state = {"user": ALICE}
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    app.dependency_overrides[get_conn] = lambda: conn

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        body = ErrorBody(outcome=exc.outcome, message=exc.message)
        return JSONResponse(body.model_dump(mode="json"), HTTP_STATUS.get(exc.outcome, 400))

    yield Env(TestClient(app), conn, state)
    conn.close()
    get_settings.cache_clear()


def add_result_and_pin(env: Env, dataset_id: str, owner: User = ALICE) -> None:
    env.conn.execute("INSERT INTO conversations VALUES ('c1', ?, 't', '{}', '2026-01-01')",
                     (owner.id,))
    env.conn.execute("INSERT INTO results VALUES ('r1', ?, 'c1', '{}', ?, '2026-01-01')",
                     (owner.id, json.dumps({dataset_id: 1})))
    env.conn.execute("INSERT INTO results VALUES ('r2', ?, 'c1', '{}', ?, '2026-01-01')",
                     (owner.id, json.dumps({"some-other-dataset": 1})))
    env.conn.execute("INSERT INTO pins VALUES ('p1', ?, 'r1', 'k1', '2026-01-01')", (owner.id,))
    env.conn.execute("INSERT INTO pins VALUES ('p2', ?, 'r2', 'k2', '2026-01-01')", (owner.id,))


# ---- upload / list / get -------------------------------------------------------------


def test_upload_several_files_and_list_them(env):
    r = env.client.post("/api/datasets", files=[
        ("files", ("orders.csv", (FIXTURES / "orders.csv").read_bytes(), "text/csv")),
        ("files", ("sales.csv", CSV, "text/csv")),
    ])
    assert r.status_code == 201
    assert [d["name"] for d in r.json()] == ["orders", "sales"]
    listed = env.client.get("/api/datasets").json()
    assert {d["name"]: d["row_count"] for d in listed}["sales"] == 3
    assert all(d["version"] == 1 for d in listed)


def test_same_name_gets_a_suffix(env):
    assert env.upload("sales.csv")["name"] == "sales"
    assert env.upload("sales.csv")["name"] == "sales_2"
    assert env.upload("sales.csv")["name"] == "sales_3"


def test_get_dataset_returns_profile_and_first_20_rows(env):
    ds = env.upload("orders.csv", (FIXTURES / "orders.csv").read_bytes())
    body = env.client.get(f"/api/datasets/{ds['id']}").json()
    assert body["profile"]["row_count"] == body["row_count"] > 4000
    assert body["preview"]["columns"][0] == "order_id"
    assert len(body["preview"]["rows"]) == 20
    assert body["preview"]["rows"][0][:2] == ["O000001", "2025-01-01"]


def test_quality_endpoint(env):
    ds = env.upload("messy_customers.csv", (FIXTURES / "messy_customers.csv").read_bytes())
    body = env.client.get(f"/api/datasets/{ds['id']}/quality").json()
    assert body["duplicate_rows"] == 3 and 0 <= body["score"] < 100


def test_bad_file_is_refused_and_nothing_is_kept(env):
    r = env.client.post("/api/datasets", files=[
        ("files", ("good.csv", CSV, "text/csv")),
        ("files", ("empty.csv", b"", "text/csv")),
    ])
    assert r.status_code == 422
    assert r.json()["outcome"] == "invalid_input" and "empty.csv" in r.json()["message"]
    assert env.client.get("/api/datasets").json() == []  # good.csv was rolled back too
    assert not (Path(get_settings().data_dir) / "datasets").exists() or not any(
        (Path(get_settings().data_dir) / "datasets").iterdir())


def test_non_csv_extension_is_refused(env):
    r = env.client.post("/api/datasets", files=[("files", ("notes.txt", CSV, "text/plain"))])
    assert r.status_code == 422 and "only .csv" in r.json()["message"]


def test_upload_over_the_size_limit_is_refused(env, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    big = b"a,b\n" + b"1,2\n" * 300_000
    r = env.client.post("/api/datasets", files=[("files", ("big.csv", big, "text/csv"))])
    assert r.status_code == 422 and "1 MB" in r.json()["message"]


# ---- replace -------------------------------------------------------------------------


def test_replace_bumps_version_and_keeps_old_parquet(env):
    ds = env.upload()
    r = env.client.put(f"/api/datasets/{ds['id']}",
                       files={"file": ("sales.csv", b"region,revenue\nEast,1\n", "text/csv")})
    assert r.status_code == 200
    assert r.json()["version"] == 2 and r.json()["row_count"] == 1
    assert r.json()["name"] == "sales"
    assert dataset_parquet_path(ds["id"], 1).exists()
    assert dataset_parquet_path(ds["id"], 2).exists()
    body = env.client.get(f"/api/datasets/{ds['id']}").json()
    assert body["preview"]["rows"] == [["East", 1]]  # reads the new version


def test_failed_replace_changes_nothing(env):
    ds = env.upload()
    r = env.client.put(f"/api/datasets/{ds['id']}", files={"file": ("x.csv", b"", "text/csv")})
    assert r.status_code == 422
    assert env.client.get(f"/api/datasets/{ds['id']}").json()["version"] == 1
    assert not dataset_parquet_path(ds["id"], 2).exists()


# ---- dependents and delete -----------------------------------------------------------


def test_dependents_count_results_and_pins_on_those_results(env):
    ds = env.upload()
    add_result_and_pin(env, ds["id"])
    r = env.client.get(f"/api/datasets/{ds['id']}/dependents")
    assert r.json() == {"dataset_id": ds["id"], "results": 1, "pins": 1}


def test_delete_without_confirm_is_refused_and_explains_dependents(env):
    ds = env.upload()
    add_result_and_pin(env, ds["id"])
    r = env.client.delete(f"/api/datasets/{ds['id']}")
    assert r.status_code == 422
    assert "1 saved result" in r.json()["message"] and "1 pinned" in r.json()["message"]
    assert env.client.get(f"/api/datasets/{ds['id']}").status_code == 200
    assert dataset_parquet_path(ds["id"], 1).exists()


def test_delete_with_confirm_removes_row_and_parquet(env):
    ds = env.upload()
    env.client.put(f"/api/datasets/{ds['id']}",
                   files={"file": ("sales.csv", CSV, "text/csv")})  # now has v1 and v2
    r = env.client.delete(f"/api/datasets/{ds['id']}", params={"confirm": "true"})
    assert r.status_code == 200
    assert env.client.get(f"/api/datasets/{ds['id']}").status_code == 404
    assert not dataset_parquet_path(ds["id"], 1).parent.exists()


# ---- ownership -----------------------------------------------------------------------


def test_other_users_dataset_is_hidden_everywhere(env):
    ds = env.upload()
    add_result_and_pin(env, ds["id"])
    env.as_user(BOB)
    base = f"/api/datasets/{ds['id']}"
    responses = {
        "get": env.client.get(base),
        "quality": env.client.get(f"{base}/quality"),
        "dependents": env.client.get(f"{base}/dependents"),
        "put": env.client.put(base, files={"file": ("sales.csv", CSV, "text/csv")}),
        "delete": env.client.delete(base, params={"confirm": "true"}),
        "delete_unconfirmed": env.client.delete(base),
    }
    for action, r in responses.items():
        assert r.status_code == 404, action
        assert r.json()["outcome"] == "not_found", action
        assert "alice" not in r.text.lower(), action  # no owner identity leaks

    assert env.client.get("/api/datasets").json() == []

    env.as_user(ALICE)  # nothing was changed by Bob's attempts
    body = env.client.get(base).json()
    assert body["version"] == 1 and dataset_parquet_path(ds["id"], 1).exists()
    assert [d["id"] for d in env.client.get("/api/datasets").json()] == [ds["id"]]


def test_same_filename_for_two_users_does_not_clash(env):
    assert env.upload("sales.csv")["name"] == "sales"
    assert env.as_user(BOB).upload("sales.csv")["name"] == "sales"
    assert len(env.client.get("/api/datasets").json()) == 1  # Bob sees only his own


def test_upload_limit_follows_admin_setting(env):
    env.conn.execute("INSERT INTO settings VALUES ('max_upload_mb', '1')")
    big = b"a,b\n" + b"1,2\n" * 300_000
    r = env.client.post("/api/datasets", files=[("files", ("big.csv", big, "text/csv"))])
    assert r.status_code == 422 and "1 MB upload limit" in r.json()["message"]


def test_concurrent_same_name_upload_picks_next_name(env, monkeypatch):
    # Live verification: two simultaneous uploads chose the same free name and one hit 500.
    from app.datasets import router as r

    env.client.post("/api/datasets", files=[("files", ("p.csv", b"a\n1\n", "text/csv"))])
    real, calls = r._unique_name, []

    def stale_then_real(conn, owner_id, stem):
        calls.append(1)
        return "p" if len(calls) == 1 else real(conn, owner_id, stem)  # first pick is taken

    monkeypatch.setattr(r, "_unique_name", stale_then_real)
    res = env.client.post("/api/datasets", files=[("files", ("p.csv", b"a\n1\n", "text/csv"))])
    assert res.status_code == 201 and res.json()[0]["name"] == "p_2"
