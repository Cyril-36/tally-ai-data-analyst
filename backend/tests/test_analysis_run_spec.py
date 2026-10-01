"""run_spec end to end: validate -> compile -> sandboxed execute -> table + caveats."""

import duckdb
import pytest

from app.analysis.compiler import compile_spec
from app.analysis.executor import run_spec
from app.config import get_settings
from app.contracts.outcomes import AppError, Outcome
from app.contracts.result import ColumnRole
from tests.test_analysis_compiler import CASES
from tests.test_analysis_data import col, make_catalog, ref, revenue_expr, sample_frames, spec


@pytest.fixture
def catalog(tmp_path):
    return make_catalog(tmp_path, sample_frames())


def caveat_codes(meta) -> list[str]:
    return [c.code for c in meta.caveats]


@pytest.mark.parametrize("name", list(CASES))
def test_every_golden_query_is_valid_duckdb(name, catalog):
    """The golden SQL must actually run, not just look right."""
    s = spec(**CASES[name])
    compiled = compile_spec(s, catalog)
    con = duckdb.connect()
    for view, path in compiled.tables.items():
        con.execute(f'CREATE TABLE "{view}" AS SELECT * FROM read_parquet(?)', [str(path)])
    con.execute(compiled.sql, compiled.params).fetchall()
    if compiled.null_check_sql:
        con.execute(compiled.null_check_sql, compiled.params).fetchall()


def test_highest_revenue_region(catalog):
    s = spec(**{**CASES["highest_revenue_region"]})
    table, meta = run_spec(s, catalog)
    assert [c.name for c in table.columns] == ["region", "revenue"]
    assert [c.role for c in table.columns] == [ColumnRole.dimension, ColumnRole.metric]
    # East = 1*100-10 + 4*100-20 = 470; the single row asked for
    assert table.rows == [["East", 470.0]]
    assert meta.row_count == 1 and not meta.truncated
    assert meta.dataset_versions == {"id0": 1}
    assert meta.params == []


def test_monthly_trend(catalog):
    table, _ = run_spec(spec(**CASES["monthly_trend"]), catalog)
    assert [c.role for c in table.columns] == [ColumnRole.time, ColumnRole.metric]
    assert table.columns[0].dtype == "DATE"
    # Jan: 2*50 + (100-10) = 190; Feb: 150 + 30 = 180; Mar: (400-20) + null = 380
    assert table.rows == [["2026-01-01", 190.0], ["2026-02-01", 180.0], ["2026-03-01", 380.0]]


def test_top_customers_via_join_with_orphan_and_null_caveats(catalog):
    s = spec(**{**CASES["top_customers_via_join"], "limit": 2})
    table, meta = run_spec(s, catalog)
    # Order 5 belongs to customer 99, who is not listed: it surfaces as an empty name, and the
    # join_orphans caveat is what tells the reader why.
    assert table.rows == [[None, 380.0], ["Ada Lovelace", 190.0]]
    assert len(table.rows) == 2 and not meta.truncated  # asked for 2, not a cap hit
    by_code = {c.code: c for c in meta.caveats}
    assert by_code["join_orphans"].count == 1
    assert by_code["nulls_excluded"].count == 1
    assert "orders.unit_price" in by_code["nulls_excluded"].message


def test_filters_run_with_bound_parameters(catalog):
    table, meta = run_spec(spec(**CASES["filters_in_between_null"]), catalog)
    assert table.rows == []  # discount_amount is never null in the sample, so nothing matches
    assert meta.params == ["West", "East", "2026-01-01", "2026-03-31", "refunded"]
    s = spec(
        base_dataset="orders",
        metrics=[{"name": "n", "agg": "count"}],
        filters=[
            {"column": ref("orders", "region"), "op": "is_null"},
            {
                "column": ref("orders", "order_date"),
                "op": "between",
                "value": ["2026-03-01", "2026-03-31"],
            },
        ],
    )
    table, _ = run_spec(s, catalog)
    assert table.rows == [[1]]


def test_hostile_filter_value_is_just_data(catalog):
    s = spec(
        base_dataset="orders",
        metrics=[{"name": "n", "agg": "count"}],
        filters=[{"column": ref("orders", "region"), "op": "eq", "value": "x'; DROP TABLE t0; --"}],
    )
    table, _ = run_spec(s, catalog)
    assert table.rows == [[0]]


def test_derived_metric_caveat_and_division_by_zero(catalog):
    s = spec(
        base_dataset="orders",
        metrics=[
            {
                "name": "discount_rate",
                "agg": "avg",
                "expr": {
                    "kind": "binop",
                    "op": "/",
                    "left": col("orders", "discount_amount"),
                    "right": {
                        "kind": "binop",
                        "op": "-",
                        "left": col("orders", "quantity"),
                        "right": {"kind": "num", "value": 1},
                    },
                },
            }
        ],
    )
    table, meta = run_spec(s, catalog)
    assert table.rows[0][0] is not None  # quantity - 1 == 0 rows did not raise
    derived = [c for c in meta.caveats if c.code == "derived_metric"]
    assert len(derived) == 1
    assert "avg(" in derived[0].message and "divisor is zero" in derived[0].message


def test_plain_metrics_have_no_derived_caveat(catalog):
    _, meta = run_spec(spec(**CASES["aggregates"]), catalog)
    assert "derived_metric" not in caveat_codes(meta)


def test_rows_truncated_only_when_the_cap_is_hit(catalog, monkeypatch):
    monkeypatch.setattr(get_settings(), "result_row_cap", 2)
    s = spec(
        base_dataset="orders",
        metrics=[{"name": "n", "agg": "count"}],
        dimensions=[ref("orders", "order_id")],
        sort=[{"by": "order_id", "descending": False}],
    )
    table, meta = run_spec(s, catalog)
    assert table.rows == [[1, 1], [2, 1]]
    assert meta.truncated and meta.row_count == 2
    truncated = [c for c in meta.caveats if c.code == "rows_truncated"]
    assert len(truncated) == 1 and truncated[0].count == 2

    # exactly the cap: nothing was cut
    exact = spec(
        **{
            **s.model_dump(mode="json"),
            "filters": [{"column": ref("orders", "order_id"), "op": "lte", "value": 2}],
        }
    )
    table, meta = run_spec(exact, catalog)
    assert len(table.rows) == 2 and not meta.truncated
    assert "rows_truncated" not in caveat_codes(meta)

    # an explicit top-k below the cap is not a truncation
    top1 = spec(**{**s.model_dump(mode="json"), "limit": 1})
    table, meta = run_spec(top1, catalog)
    assert len(table.rows) == 1 and not meta.truncated


def test_invalid_spec_never_reaches_the_executor(catalog):
    s = spec(
        base_dataset="orders",
        metrics=[{"name": "m", "agg": "sum", "expr": col("orders", "region")}],
    )
    with pytest.raises(AppError) as err:
        run_spec(s, catalog)
    assert err.value.outcome == Outcome.invalid_input


def test_only_catalog_datasets_are_reachable(catalog):
    visible = {"customers": catalog["customers"]}
    s = spec(
        base_dataset="orders",
        metrics=[{"name": "revenue", "agg": "sum", "expr": revenue_expr()}],
    )
    with pytest.raises(AppError) as err:
        run_spec(s, visible)
    assert "Unknown dataset 'orders'" in err.value.message
