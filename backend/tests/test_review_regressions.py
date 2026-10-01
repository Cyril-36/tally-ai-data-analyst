import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analysis.executor import run_spec
from app.analysis.validator import validate
from app.auth.passwords import ensure_admin
from app.contracts.outcomes import AppError
from app.db import connect, init_db
from app.main import create_app
from tests.test_analysis_data import col, make_catalog, ref, spec


def test_time_bucket_named_dimension_sorts_without_synthetic_period(tmp_path):
    catalog = make_catalog(
        tmp_path, {"data": pd.DataFrame({"time_bucket": ["b", "a"], "amount": [2, 3]})}
    )
    s = spec(
        base_dataset="data",
        metrics=[{"name": "total", "agg": "sum", "expr": col("data", "amount")}],
        dimensions=[ref("data", "time_bucket")],
        sort=[{"by": "time_bucket", "descending": False}],
    )
    table, _ = run_spec(s, catalog)
    assert table.rows == [["a", 3], ["b", 2]]


def test_time_bucket_metric_alias_is_valid_without_time_grouping(tmp_path):
    catalog = make_catalog(tmp_path, {"data": pd.DataFrame({"amount": [2, 3]})})
    table, _ = run_spec(
        spec(
            base_dataset="data",
            metrics=[{"name": "time_bucket", "agg": "sum", "expr": col("data", "amount")}],
            sort=[{"by": "time_bucket"}],
        ),
        catalog,
    )
    assert table.rows == [[5]]


def test_reserved_sort_token_cannot_shadow_time_grouping(tmp_path):
    catalog = make_catalog(
        tmp_path,
        {"data": pd.DataFrame({"time_bucket": ["a"], "day": pd.to_datetime(["2026-01-01"])})},
    )
    s = spec(
        base_dataset="data",
        metrics=[{"name": "n", "agg": "count"}],
        dimensions=[ref("data", "time_bucket")],
        time_bucket={"column": ref("data", "day"), "grain": "month"},
    )
    with pytest.raises(AppError, match="reserved"):
        validate(s, catalog)


def test_login_limit_is_shared_across_app_instances_and_expires(client, db):
    other = TestClient(create_app())
    body = {"email": "missing@example.com", "password": "wrong-password"}
    headers = {"X-Requested-With": "test"}
    for _ in range(5):
        assert client.post("/api/auth/login", json=body, headers=headers).status_code == 401
    assert other.post("/api/auth/login", json=body, headers=headers).status_code == 429
    db.execute("UPDATE login_attempts SET attempted_at = 0")
    assert other.post("/api/auth/login", json=body, headers=headers).status_code == 401
    assert db.execute("SELECT count(*) FROM login_attempts").fetchone()[0] == 1


def test_parallel_worker_startup_migrates_pins_and_seeds_admin_once(tmp_path):
    path = tmp_path / "shared.sqlite3"
    with sqlite3.connect(path) as old:
        old.execute(
            "CREATE TABLE pins (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, "
            "result_id TEXT NOT NULL, idempotency_key TEXT, created_at TEXT NOT NULL)"
        )

    def start():
        init_db(path)
        with connect(path) as conn:
            ensure_admin(conn, "admin@example.com", "a-local-admin-password")

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: start(), range(2)))
    with connect(path) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(pins)")}
        assert {"title", "position"} <= columns
        assert (
            conn.execute("SELECT count(*) FROM users WHERE email = 'admin@example.com'").fetchone()[
                0
            ]
            == 1
        )
