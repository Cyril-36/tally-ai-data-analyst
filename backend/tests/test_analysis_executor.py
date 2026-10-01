"""The executor is the security boundary for compiled SQL: these tests try to break out."""

import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import duckdb
import pytest

from app.analysis import executor
from app.analysis.executor import _short, execute
from app.config import get_settings
from app.contracts.outcomes import AppError, Outcome
from tests.test_analysis_data import make_catalog, sample_frames

# Vars the interpreter/OS add themselves (not inherited from the parent, not secrets).
INTERPRETER_ENV = {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}

SLOW_QUERY = (
    "SELECT COUNT(*) FROM range(1000000) a, range(1000000) b WHERE (a.range * b.range) % 7 = 3"
)


@pytest.fixture
def catalog(tmp_path):
    return make_catalog(tmp_path, sample_frames())


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Point the executor's temp dirs at a directory the test can inspect afterwards."""
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    monkeypatch.chdir(tmp_path)
    return root


def failure(sql: str, tables) -> str:
    with pytest.raises(AppError) as err:
        execute(sql, [], tables)
    assert err.value.outcome == Outcome.execution_failed
    return err.value.message


def test_runs_a_query_and_returns_typed_columns(catalog, scratch):
    out = execute(
        "SELECT region, SUM(quantity) AS q FROM t0 WHERE quantity > ? GROUP BY region ORDER BY 1",
        [1],
        {"t0": catalog["orders"].parquet_path},
    )
    assert out.main.columns == [("region", "VARCHAR"), ("q", "HUGEINT")]
    assert out.main.rows == [["East", 4], ["West", 5], [None, 2]]
    assert list(scratch.iterdir()) == []  # temp dir removed after success too


def test_extra_queries_run_in_the_same_worker(catalog):
    t = {"t0": catalog["orders"].parquet_path}
    out = execute("SELECT COUNT(*) FROM t0", [], t, [("SELECT MAX(order_id) FROM t0", [])])
    assert out.main.rows == [[6]]
    assert out.extra[0].rows == [[6]]


def test_values_are_json_safe(catalog):
    out = execute(
        "SELECT DATE '2026-01-31' AS d, 'nan'::DOUBLE AS x, 1.25::DECIMAL(5,2) AS y",
        [],
        {"t0": catalog["orders"].parquet_path},
    )
    assert out.main.rows == [["2026-01-31", None, 1.25]]


# ---- sandbox escapes ------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_text('/etc/passwd')",
        "SELECT * FROM glob('/*')",
        "COPY (SELECT 1) TO 'x.csv'",
        "COPY t0 TO 'x.parquet' (FORMAT PARQUET)",
        "INSTALL httpfs",
        "LOAD httpfs",
        "ATTACH 'x.db'",
        "SET enable_external_access=true",
        "SET lock_configuration=false",
        "SET autoload_known_extensions=true",
        "SELECT * FROM read_parquet('/etc/passwd')",
    ],
)
def test_sandbox_escapes_fail(sql, catalog, scratch):
    message = failure(sql, {"t0": catalog["orders"].parquet_path})
    # Refused by the sandbox, not by a typo or a missing table.
    assert any(w in message for w in ("disabled by configuration", "disabled through", "locked"))
    assert str(scratch) not in message  # no temp paths leak
    assert not (scratch.parent / "x.csv").exists()
    assert not (scratch.parent / "x.parquet").exists()
    assert not (scratch.parent / "x.db").exists()


def test_escape_tests_are_meaningful_without_the_sandbox(tmp_path):
    # The same read works in a plain DuckDB connection, so the failures above are the sandbox.
    target = tmp_path / "secret.csv"
    target.write_text("a\n1\n")
    assert duckdb.connect().execute(f"SELECT * FROM read_csv('{target}')").fetchall() == [(1,)]
    with pytest.raises(AppError) as err:
        execute(f"SELECT * FROM read_csv('{target}')", [], {})
    assert "disabled by configuration" in err.value.message
    assert str(tmp_path) not in err.value.message


def test_settings_cannot_be_changed_back(catalog):
    message = failure("SET enable_external_access=true", {"t0": catalog["orders"].parquet_path})
    assert "locked" in message


def test_worker_environment_is_only_path_and_tz(catalog, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("APP_SECRET_KEY", "also-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///secret.db")
    out = execute("SELECT 1", [], {"t0": catalog["orders"].parquet_path})
    keys = set(out.meta["env_keys"])
    assert {"PATH", "TZ"} <= keys
    assert keys <= {"PATH", "TZ"} | INTERPRETER_ENV
    for name in ("LLM_API_KEY", "APP_SECRET_KEY", "DATABASE_URL", "HOME"):
        assert name not in keys


def test_worker_runs_in_a_fresh_temp_dir_that_is_removed(catalog, scratch):
    out = execute("SELECT 1", [], {"t0": catalog["orders"].parquet_path})
    cwd = out.meta["cwd"]
    assert cwd.startswith(str(scratch))
    assert not scratch.joinpath(cwd).exists()
    assert list(scratch.iterdir()) == []


def test_rlimits_that_the_os_allows_are_applied(catalog):
    out = execute("SELECT 1", [], {"t0": catalog["orders"].parquet_path})
    applied = out.meta["limits"]
    # macOS refuses RLIMIT_AS; the others are enforced there and on Linux.
    assert {"RLIMIT_CPU", "RLIMIT_NOFILE", "RLIMIT_FSIZE"} <= set(applied)
    assert applied["RLIMIT_FSIZE"] == 0
    if sys.platform.startswith("linux"):
        assert "RLIMIT_AS" in applied


# ---- resource control -----------------------------------------------------------------


def test_slow_query_is_killed_at_the_timeout_and_temp_dir_removed(catalog, scratch, monkeypatch):
    monkeypatch.setattr(get_settings(), "query_timeout_s", 2.0)
    started: list[subprocess.Popen] = []

    class Recording(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            started.append(self)

    monkeypatch.setattr(executor.subprocess, "Popen", Recording)

    begin = time.perf_counter()
    with pytest.raises(AppError) as err:
        execute(SLOW_QUERY, [], {"t0": catalog["orders"].parquet_path})
    elapsed = time.perf_counter() - begin

    assert err.value.outcome == Outcome.execution_failed
    assert err.value.message == "Query exceeded the time limit"
    assert elapsed < 8
    assert len(started) == 1 and started[0].poll() is not None  # worker is dead
    assert list(scratch.iterdir()) == []


def test_memory_hungry_query_fails_cleanly(catalog, scratch, monkeypatch):
    monkeypatch.setattr(get_settings(), "worker_memory_mb", 128)
    message = failure(
        "SELECT LENGTH(string_agg(repeat('x', 1000000), '')) FROM range(2000)",
        {"t0": catalog["orders"].parquet_path},
    )
    assert message.startswith("Query failed: Out of Memory")
    assert list(scratch.iterdir()) == []


def test_sql_errors_are_short_and_have_no_paths(catalog):
    message = failure("SELEC 1", {"t0": catalog["orders"].parquet_path})
    assert message.startswith("Query failed: Parser Error")
    assert "\n" not in message


def test_error_messages_hide_paths():
    raw = 'IO Error: Cannot open file "/private/var/x/analysis-ab/t0.parquet": denied\nstack'
    assert _short(raw) == 'IO Error: Cannot open file "<path>": denied'
    assert _short("a/b and/or c") == "a/b and/or c"


# ---- isolation between requests -------------------------------------------------------


def test_concurrent_executions_do_not_see_each_others_tables(catalog):
    orders = {"t0": catalog["orders"].parquet_path}
    customers = {"t0": catalog["customers"].parquet_path, "t9": catalog["payments"].parquet_path}

    def a(_):
        count = execute("SELECT COUNT(*) FROM t0", [], orders).main.rows[0][0]
        with pytest.raises(AppError) as err:
            execute("SELECT * FROM t9", [], orders)  # B's table name
        return count, err.value.message

    def b(_):
        count = execute("SELECT COUNT(*) FROM t9", [], customers).main.rows[0][0]
        with pytest.raises(AppError) as err:
            execute("SELECT order_id FROM t0", [], customers)  # A's column
        return count, err.value.message

    with ThreadPoolExecutor(4) as pool:
        a_results = list(pool.map(a, range(2)))
        b_results = list(pool.map(b, range(2)))
        mixed = [pool.submit(a, 0), pool.submit(b, 0), pool.submit(a, 1), pool.submit(b, 1)]
        mixed_results = [f.result() for f in mixed]

    assert {r[0] for r in a_results} == {6}
    assert {r[0] for r in b_results} == {4}
    assert [r[0] for r in mixed_results] == [6, 4, 6, 4]
    for _, message in a_results + b_results + mixed_results:
        assert message.startswith("Query failed")


def test_no_state_survives_between_requests(catalog):
    t = {"t0": catalog["orders"].parquet_path}
    execute("CREATE TABLE leftover AS SELECT 1 AS x", [], t)
    message = failure("SELECT * FROM leftover", t)
    assert "does not exist" in message


def test_source_parquet_is_not_modified(catalog):
    path = catalog["orders"].parquet_path
    before = path.read_bytes()
    execute("SELECT * FROM t0", [], {"t0": path})
    assert path.read_bytes() == before
