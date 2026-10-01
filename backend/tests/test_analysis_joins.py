import duckdb
import pandas as pd
import pytest

from app.analysis.executor import execute, run_spec
from app.analysis.joins import check_join
from app.contracts.outcomes import AppError, Outcome
from app.contracts.spec import Join
from tests.test_analysis_data import col, make_catalog, ref, sample_frames, spec


@pytest.fixture
def catalog(tmp_path):
    return make_catalog(tmp_path, sample_frames())


def join(left: tuple[str, str], right: tuple[str, str], **extra) -> Join:
    return Join.model_validate({"left": ref(*left), "right": ref(*right), **extra})


PRE_AGG = {"metrics": [{"name": "paid", "agg": "sum", "expr": col("payments", "amount")}]}


def test_many_to_one_is_allowed_and_reports_orphans(catalog):
    check = check_join(
        join(("orders", "customer_id"), ("customers", "customer_id")), catalog, execute
    )
    assert check.allowed and check.reason is None
    assert check.right_unique and not check.left_unique
    assert check.right_null_keys == 0
    assert check.orphan_left_rows == 1  # order 5 belongs to customer 99, who is not listed


def test_payments_fan_out_is_refused(catalog):
    check = check_join(join(("orders", "order_id"), ("payments", "order_id")), catalog, execute)
    assert not check.allowed
    assert not check.right_unique and check.left_unique
    assert "orders" in check.reason and "payments" in check.reason
    assert "inflate" in check.reason and "pre_aggregate" in check.reason
    assert check.orphan_left_rows == 3  # orders 4, 5 and 6 have no payments


def test_pre_aggregate_makes_the_fan_out_join_allowed(catalog):
    check = check_join(
        join(("orders", "order_id"), ("payments", "order_id"), pre_aggregate=PRE_AGG),
        catalog,
        execute,
    )
    assert check.allowed and check.reason is None
    assert not check.right_unique  # still measured honestly


def test_duplicated_dimension_key_is_refused(catalog):
    check = check_join(
        join(("orders", "customer_id"), ("dup_dim", "customer_id")), catalog, execute
    )
    assert not check.allowed
    assert not check.right_unique
    assert "dup_dim" in check.reason


def test_null_keys_do_not_count_as_duplicates_but_are_reported(tmp_path):
    catalog = make_catalog(
        tmp_path,
        {
            "facts": pd.DataFrame({"k": [1, 2, None], "v": [1.0, 2.0, 3.0]}),
            "dims": pd.DataFrame({"k": [1.0, 2.0, None, None], "label": list("abcd")}),
        },
    )
    check = check_join(join(("facts", "k"), ("dims", "k")), catalog, execute)
    assert check.allowed and check.right_unique
    assert check.right_null_keys == 2
    assert check.orphan_left_rows == 1  # the fact row with a null key matches nothing


def test_check_join_rejects_unknown_columns_and_datasets(catalog):
    with pytest.raises(AppError) as err:
        check_join(join(("orders", "nope"), ("customers", "customer_id")), catalog, execute)
    assert err.value.outcome == Outcome.invalid_input
    with pytest.raises(AppError):
        check_join(join(("orders", "order_id"), ("ghost", "order_id")), catalog, execute)


# ---- totals: the reason the rule exists ------------------------------------------------


@pytest.fixture
def one_order(tmp_path):
    """A GBP 100 order paid in two instalments."""
    return make_catalog(
        tmp_path,
        {
            "orders": pd.DataFrame(
                {"order_id": [1], "region": ["West"], "total": [100.0]},
            ),
            "payments": pd.DataFrame({"order_id": [1, 1], "amount": [60.0, 40.0]}),
        },
    )


def test_naive_join_would_double_count(one_order):
    # The control: without the rule, joining raw payments turns 100 into 200.
    con = duckdb.connect()
    for name, info in one_order.items():
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{info.parquet_path}')")
    naive = con.execute(
        "SELECT SUM(o.total) FROM orders o LEFT JOIN payments p ON o.order_id = p.order_id"
    ).fetchone()
    assert naive == (200.0,)


def test_run_spec_refuses_the_fan_out_join(one_order):
    s = spec(
        base_dataset="orders",
        metrics=[{"name": "total", "agg": "sum", "expr": col("orders", "total")}],
        joins=[{"left": ref("orders", "order_id"), "right": ref("payments", "order_id")}],
    )
    with pytest.raises(AppError) as err:
        run_spec(s, one_order)
    assert err.value.outcome == Outcome.unsupported
    assert "inflate" in err.value.message


def test_run_spec_with_pre_aggregate_keeps_the_order_total(one_order):
    s = spec(
        base_dataset="orders",
        metrics=[
            {"name": "total", "agg": "sum", "expr": col("orders", "total")},
            {"name": "paid", "agg": "sum", "expr": col("payments", "paid")},
        ],
        dimensions=[ref("orders", "region")],
        joins=[
            {
                "left": ref("orders", "order_id"),
                "right": ref("payments", "order_id"),
                "pre_aggregate": PRE_AGG,
            }
        ],
    )
    table, meta = run_spec(s, one_order)
    assert table.rows == [["West", 100.0, 100.0]]  # not 200
    assert not meta.caveats
