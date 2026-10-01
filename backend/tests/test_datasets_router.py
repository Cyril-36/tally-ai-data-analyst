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
    env.conn.execute("INSERT INTO pins (id, owner_id, result_id, idempotency_key, created_at)"
                     " VALUES ('p1', ?, 'r1', 'k1', '2026-01-01')", (owner.id,))
    env.conn.execute("INSERT INTO pins (id, owner_id, result_id, idempotency_key, created_at)"
                     " VALUES ('p2', ?, 'r2', 'k2', '2026-01-01')", (owner.id,))


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


# ---- rename --------------------------------------------------------------------------


def test_rename_changes_name_and_updated_at_but_not_version_or_data(env):
    ds = env.upload()
    r = env.client.patch(f"/api/datasets/{ds['id']}", json={"name": "  Q3 sales "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Q3 sales" and body["version"] == 1 and body["row_count"] == 3
    assert body["updated_at"] > ds["updated_at"] and body["created_at"] == ds["created_at"]
    assert [d["name"] for d in env.client.get("/api/datasets").json()] == ["Q3 sales"]
    preview = env.client.get(f"/api/datasets/{ds['id']}").json()["preview"]
    assert preview["rows"][0] == ["North", 10.5]
    assert dataset_parquet_path(ds["id"], 1).exists()
    # renaming to the current name is not a clash
    same = env.client.patch(f"/api/datasets/{ds['id']}", json={"name": "Q3 sales"})
    assert same.status_code == 200


def test_rename_to_an_existing_name_is_refused_but_only_per_owner(env):
    a, b = env.upload("a.csv"), env.upload("b.csv")
    r = env.client.patch(f"/api/datasets/{b['id']}", json={"name": "a"})
    assert r.status_code == 422 and r.json() == {
        "outcome": "invalid_input", "message": "You already have a dataset named 'a'."}
    assert env.client.get(f"/api/datasets/{b['id']}").json()["name"] == "b"

    carol = env.as_user(BOB).upload("c.csv")  # Bob may take a name Alice already uses
    assert env.client.patch(f"/api/datasets/{carol['id']}", json={"name": "a"}).status_code == 200
    env.as_user(ALICE)
    assert env.client.patch(f"/api/datasets/{a['id']}", json={"name": "c"}).status_code == 200


@pytest.mark.parametrize("name", ["", "   ", "x" * 129, None, 5])
def test_rename_validates_the_name(env, name):
    ds = env.upload()
    r = env.client.patch(f"/api/datasets/{ds['id']}", json={"name": name})
    assert r.status_code == 422
    assert env.client.get(f"/api/datasets/{ds['id']}").json()["name"] == "sales"


def test_rename_accepts_the_length_limits(env):
    ds = env.upload()
    url = f"/api/datasets/{ds['id']}"
    assert env.client.patch(url, json={"name": "x" * 128}).status_code == 200
    assert env.client.patch(url, json={"name": "y"}).status_code == 200


# ---- rows ----------------------------------------------------------------------------


def test_rows_are_paged_with_total_and_json_safe_values(env):
    ds = env.upload("orders.csv", (FIXTURES / "orders.csv").read_bytes())
    base = f"/api/datasets/{ds['id']}/rows"
    first = env.client.get(base).json()
    assert first["offset"] == 0 and first["limit"] == 50 and first["total"] == ds["row_count"]
    assert first["columns"][0] == "order_id" and len(first["rows"]) == 50
    assert first["rows"][0][:2] == ["O000001", "2025-01-01"]  # dates are ISO strings
    page = env.client.get(base, params={"offset": 10, "limit": 5}).json()
    assert page["rows"] == first["rows"][10:15] and page["offset"] == 10 and page["limit"] == 5
    tail = env.client.get(base, params={"offset": ds["row_count"] - 2, "limit": 200}).json()
    assert len(tail["rows"]) == 2
    beyond = env.client.get(base, params={"offset": ds["row_count"] + 5}).json()
    assert beyond["rows"] == [] and beyond["total"] == ds["row_count"]
    preview = env.client.get(f"/api/datasets/{ds['id']}").json()["preview"]
    assert preview["rows"] == first["rows"][:20]


def test_rows_read_the_current_version(env):
    ds = env.upload()
    env.client.put(f"/api/datasets/{ds['id']}",
                   files={"file": ("sales.csv", b"region,revenue\nEast,1\n", "text/csv")})
    body = env.client.get(f"/api/datasets/{ds['id']}/rows").json()
    assert body["rows"] == [["East", 1]] and body["total"] == 1


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"limit": "x"}])
def test_rows_paging_bounds(env, params):
    ds = env.upload()
    assert env.client.get(f"/api/datasets/{ds['id']}/rows", params=params).status_code == 422


# ---- sample data ---------------------------------------------------------------------

SAMPLES = ["orders", "customers", "products", "payments"]


def test_sample_loads_the_four_datasets_through_the_normal_path(env):
    r = env.client.post("/api/datasets/sample")
    assert r.status_code == 201, r.text
    assert [d["name"] for d in r.json()["created"]] == SAMPLES and r.json()["existing"] == []
    assert all(d["version"] == 1 and d["filename"] == f"{d['name']}.csv"
               for d in r.json()["created"])
    listed = {d["name"]: d for d in env.client.get("/api/datasets").json()}
    assert listed["orders"]["row_count"] == 4971 and listed["customers"]["row_count"] == 400
    profile = env.client.get(f"/api/datasets/{listed['orders']['id']}").json()["profile"]
    assert profile["row_count"] == 4971  # profiled like an upload


def test_sample_is_idempotent_and_only_adds_what_is_missing(env):
    first = env.client.post("/api/datasets/sample").json()["created"]
    again = env.client.post("/api/datasets/sample")
    assert again.status_code == 200
    assert again.json()["created"] == []
    assert [d["id"] for d in again.json()["existing"]] == [d["id"] for d in first]
    assert [d["name"] for d in env.client.get("/api/datasets").json()] == SAMPLES

    gone = next(d for d in first if d["name"] == "products")
    env.client.delete(f"/api/datasets/{gone['id']}", params={"confirm": "true"})
    partial = env.client.post("/api/datasets/sample").json()
    assert [d["name"] for d in partial["created"]] == ["products"]
    assert sorted(d["name"] for d in partial["existing"]) == ["customers", "orders", "payments"]
    names = [d["name"] for d in env.client.get("/api/datasets").json()]
    assert len(names) == 4 and not any(n.endswith("_2") for n in names)


def test_sample_is_per_user(env):
    env.client.post("/api/datasets/sample")
    r = env.as_user(BOB).client.post("/api/datasets/sample")
    assert r.status_code == 201 and len(r.json()["created"]) == 4
    assert len(env.client.get("/api/datasets").json()) == 4  # Bob's copies are Bob's


def test_sample_is_refused_when_an_admin_turned_it_off(env):
    env.conn.execute("INSERT INTO settings VALUES ('sample_data_enabled', 'false')")
    r = env.client.post("/api/datasets/sample")
    assert r.status_code == 403
    assert r.json() == {"outcome": "permission_denied",
                        "message": "Sample data is turned off by an admin."}
    assert env.client.get("/api/datasets").json() == []


def test_sample_failure_keeps_nothing_and_hides_paths(env, tmp_path, monkeypatch):
    broken = tmp_path / "sample"
    broken.mkdir()
    (broken / "orders.csv").write_bytes(CSV)  # customers.csv etc. are missing
    monkeypatch.setenv("SAMPLE_DIR", str(broken))
    get_settings.cache_clear()
    r = env.client.post("/api/datasets/sample")
    assert r.status_code == 500 and r.json()["outcome"] == "execution_failed"
    assert str(broken) not in r.text
    assert env.client.get("/api/datasets").json() == []  # orders was rolled back too
    root = Path(get_settings().data_dir) / "datasets"
    assert not root.exists() or not any(root.iterdir())


# ---- quality examples ----------------------------------------------------------------


@pytest.fixture
def messy(env, monkeypatch):
    monkeypatch.setenv("DEMO_REFERENCE_DATE", "2026-09-30")
    get_settings.cache_clear()
    ds = env.upload("messy_customers.csv", (FIXTURES / "messy_customers.csv").read_bytes())
    return env, ds


def examples(env, ds, check, column=None):
    params = {"check": check, **({"column": column} if column else {})}
    return env.client.get(f"/api/datasets/{ds['id']}/quality/examples", params=params)


def test_examples_follow_the_quality_report_definitions(messy):
    env, ds = messy
    report = env.client.get(f"/api/datasets/{ds['id']}/quality").json()
    by_check = {"nulls": "nulls", "numeric_text": "numeric_stored_as_text",
                "future_dates": "future_dates", "variants": "category_variants"}
    seen = set()
    for check, code in by_check.items():
        for issue in (i for i in report["issues"] if i["code"] == code):
            body = examples(env, ds, check, issue["column"]).json()
            seen.add(check)
            assert body["check"] == check and body["column"] == issue["column"]
            assert body["columns"] == [c["name"] for c in report["columns"]]
            if check != "variants":  # variants shows one row per spelling, not per row
                assert len(body["rows"]) == min(5, issue["count"]), (check, issue)
            assert 1 <= len(body["rows"]) <= 5
    assert seen == set(by_check)  # the fixture exercises every column-based check


def test_example_rows_really_contain_the_issue(messy):
    env, ds = messy
    nulls = examples(env, ds, "nulls", "email").json()
    idx = {c: i for i, c in enumerate(nulls["columns"])}
    assert all(r[idx["email"]] is None for r in nulls["rows"])

    for row in examples(env, ds, "numeric_text", "lifetime_value").json()["rows"]:
        with pytest.raises(ValueError):
            float(row[idx["lifetime_value"]])

    future = examples(env, ds, "future_dates", "signup_date").json()["rows"]
    assert future and all(r[idx["signup_date"]] > "2026-09-30" for r in future)

    spellings = [r[idx["country"]] for r in examples(env, ds, "variants", "country").json()["rows"]]
    assert len(spellings) == len(set(spellings)) >= 2
    assert {s.strip().casefold().replace(".", "") for s in spellings} <= {"france", "germany", "uk"}

    dup = examples(env, ds, "duplicates").json()
    assert 1 <= len(dup["rows"]) <= 5 and dup["column"] is None
    assert len({tuple(map(str, r)) for r in dup["rows"]}) == len(dup["rows"])  # one per group


def test_negative_example_and_empty_example(env):
    ds = env.upload("orders.csv", (FIXTURES / "orders.csv").read_bytes())
    neg = examples(env, ds, "negatives", "quantity").json()
    q = neg["columns"].index("quantity")
    assert len(neg["rows"]) == 1 and neg["rows"][0][q] < 0
    assert examples(env, ds, "nulls", "order_id").json()["rows"] == []
    assert examples(env, ds, "numeric_text", "region").json()["rows"] == []  # plain text column


@pytest.mark.parametrize("check,column", [
    ("bogus", "email"), ("nulls", "no_such_column"), ("nulls", None),
    ("negatives", "email"), ("future_dates", "email"), ("numeric_text", "signup_date"),
])
def test_examples_reject_unknown_or_inapplicable_input(messy, check, column):
    env, ds = messy
    r = examples(env, ds, check, column)
    assert r.status_code == 422 and r.json()["outcome"] == "invalid_input"


def test_examples_require_the_check_parameter(messy):
    env, ds = messy
    assert env.client.get(f"/api/datasets/{ds['id']}/quality/examples").status_code == 422


# ---- ownership of the new routes -----------------------------------------------------


def test_other_users_dataset_is_hidden_from_rename_rows_and_examples(env):
    ds = env.upload("messy_customers.csv", (FIXTURES / "messy_customers.csv").read_bytes())
    env.as_user(BOB)
    base = f"/api/datasets/{ds['id']}"
    responses = {
        "rename": env.client.patch(base, json={"name": "mine"}),
        "rows": env.client.get(f"{base}/rows"),
        "examples": env.client.get(f"{base}/quality/examples",
                                   params={"check": "nulls", "column": "email"}),
        "examples_dups": env.client.get(f"{base}/quality/examples", params={"check": "duplicates"}),
    }
    for action, r in responses.items():
        assert r.status_code == 404, action
        assert r.json()["outcome"] == "not_found", action
        assert "alice" not in r.text.lower(), action
    env.as_user(ALICE)
    assert env.client.get(base).json()["name"] == "messy_customers"
