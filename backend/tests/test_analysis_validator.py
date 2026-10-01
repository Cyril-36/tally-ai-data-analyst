import pandas as pd
import pytest

from app.analysis.validator import validate
from app.contracts.outcomes import AppError, Outcome
from tests.test_analysis_compiler import CASES
from tests.test_analysis_data import col, make_catalog, ref, sample_frames, spec

COUNT = {"name": "n", "agg": "count"}


@pytest.fixture
def catalog(tmp_path):
    return make_catalog(tmp_path, sample_frames())


def rejected(catalog, **fields) -> str:
    with pytest.raises(AppError) as err:
        validate(spec(**fields), catalog)
    assert err.value.outcome == Outcome.invalid_input
    return err.value.message


@pytest.mark.parametrize("name", list(CASES))
def test_valid_specs_pass(name, catalog):
    validate(spec(**CASES[name]), catalog)


def test_unknown_dataset_lists_available_ones(catalog):
    msg = rejected(catalog, base_dataset="sales", metrics=[COUNT])
    assert "Unknown dataset 'sales'" in msg
    assert "orders" in msg and "customers" in msg


def test_not_authorized_dataset_looks_like_unknown_dataset(catalog):
    # The catalog only holds authorized datasets, so removing one is how "someone else's"
    # dataset appears. The message must not distinguish it from a dataset that never existed.
    visible = {k: v for k, v in catalog.items() if k != "payments"}
    hidden = rejected(visible, base_dataset="payments", metrics=[COUNT])
    missing = rejected(visible, base_dataset="nonexistent", metrics=[COUNT])
    assert hidden.replace("payments", "X") == missing.replace("nonexistent", "X")


def test_unknown_column_lists_valid_columns(catalog):
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[{"name": "t", "agg": "sum", "expr": col("orders", "revenue")}],
    )
    assert "Unknown column 'revenue' in dataset 'orders'" in msg
    assert "unit_price (DOUBLE)" in msg and "quantity (BIGINT)" in msg


def test_column_of_dataset_not_in_query(catalog):
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[COUNT],
        dimensions=[ref("customers", "segment")],
    )
    assert "not part of this query" in msg and "join" in msg


@pytest.mark.parametrize("agg", ["sum", "avg", "median", "min", "max"])
def test_numeric_aggregates_reject_text(catalog, agg):
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[{"name": "m", "agg": agg, "expr": col("orders", "region")}],
    )
    assert "needs a numeric expression" in msg and "VARCHAR" in msg


def test_count_and_count_distinct_accept_text(catalog):
    validate(
        spec(
            base_dataset="orders",
            metrics=[
                {"name": "a", "agg": "count", "expr": col("orders", "region")},
                {"name": "b", "agg": "count_distinct", "expr": col("orders", "region")},
            ],
        ),
        catalog,
    )


def test_arithmetic_on_text_is_rejected(catalog):
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[
            {
                "name": "m",
                "agg": "sum",
                "expr": {
                    "kind": "binop",
                    "op": "+",
                    "left": col("orders", "quantity"),
                    "right": col("orders", "status"),
                },
            }
        ],
    )
    assert "numeric operands" in msg and "orders.status" in msg


@pytest.mark.parametrize("agg", ["sum", "avg", "median", "min", "max", "count_distinct"])
def test_missing_expr_only_valid_for_count(catalog, agg):
    msg = rejected(catalog, base_dataset="orders", metrics=[{"name": "m", "agg": agg}])
    assert "needs an expression" in msg


def test_time_bucket_needs_a_date_column(catalog):
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[COUNT],
        time_bucket={"column": ref("orders", "region"), "grain": "month"},
    )
    assert "date or timestamp column" in msg
    assert "orders.order_date (DATE)" in msg


@pytest.mark.parametrize(
    ("column", "op", "value"),
    [
        ("quantity", "eq", "two"),  # text for a number
        ("quantity", "eq", True),  # bool is not a number
        ("region", "eq", 5),  # number for text
        ("order_date", "gte", "last month"),  # not a date
        ("order_date", "gte", "2026-13-45"),  # impossible date
        ("order_date", "gte", "2026-01-01 10:00:00"),  # timestamp for a DATE column
        ("order_date", "gte", 20260101),
        ("quantity", "eq", [1, 2]),  # list for a scalar op
        ("quantity", "eq", None),
        ("region", "in", "West"),  # scalar for a list op
        ("region", "in", []),
        ("quantity", "between", [1, 2, 3]),
        ("quantity", "between", [1]),
        ("quantity", "is_null", 1),
        ("quantity", "in", [1, "2"]),
    ],
)
def test_filter_value_mismatch(catalog, column, op, value):
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[COUNT],
        filters=[{"column": ref("orders", column), "op": op, "value": value}],
    )
    assert f"orders.{column}" in msg


def test_filter_on_boolean_column(tmp_path):
    catalog = make_catalog(tmp_path, {"flags": pd.DataFrame({"active": [True, False]})})
    ok = {"column": ref("flags", "active"), "op": "eq", "value": True}
    validate(spec(base_dataset="flags", metrics=[COUNT], filters=[ok]), catalog)
    for bad in (
        {"column": ref("flags", "active"), "op": "eq", "value": "yes"},
        {"column": ref("flags", "active"), "op": "gt", "value": True},
    ):
        rejected(catalog, base_dataset="flags", metrics=[COUNT], filters=[bad])


def test_timestamp_filter_accepts_date_and_datetime_strings(catalog):
    for value in ("2025-06-01", "2025-06-01 09:30:00", "2025-06-01T09:30"):
        validate(
            spec(
                base_dataset="customers",
                metrics=[COUNT],
                filters=[{"column": ref("customers", "signup_at"), "op": "gte", "value": value}],
            ),
            catalog,
        )


def test_join_left_must_already_be_in_the_query(catalog):
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[COUNT],
        joins=[{"left": ref("customers", "customer_id"), "right": ref("payments", "order_id")}],
    )
    assert "not in the query yet" in msg and "orders" in msg


def test_join_chain_through_a_joined_dataset_is_allowed(catalog):
    chained = [
        {"left": ref("orders", "customer_id"), "right": ref("customers", "customer_id")},
        {"left": ref("customers", "customer_id"), "right": ref("dup_dim", "customer_id")},
    ]
    validate(spec(base_dataset="orders", metrics=[COUNT], joins=chained), catalog)


def test_join_rejects_unknown_key_and_repeated_dataset(catalog):
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[COUNT],
        joins=[{"left": ref("orders", "customer_id"), "right": ref("customers", "cust")}],
    )
    assert "Unknown column 'cust' in dataset 'customers'" in msg
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[COUNT],
        joins=[{"left": ref("orders", "customer_id"), "right": ref("orders", "customer_id")}],
    )
    assert "already in the query" in msg


def test_join_rejects_incompatible_key_types(catalog):
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[COUNT],
        joins=[{"left": ref("orders", "region"), "right": ref("customers", "customer_id")}],
    )
    assert "incompatible types" in msg


def test_after_pre_aggregate_only_rolled_up_columns_exist(catalog):
    join = {
        "left": ref("orders", "order_id"),
        "right": ref("payments", "order_id"),
        "pre_aggregate": {
            "metrics": [{"name": "paid", "agg": "sum", "expr": col("payments", "amount")}]
        },
    }
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[{"name": "t", "agg": "sum", "expr": col("payments", "amount")}],
        joins=[join],
    )
    assert "Valid columns: paid (DOUBLE)" in msg


def test_pre_aggregate_names_must_not_clash_with_the_key(catalog):
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[COUNT],
        joins=[
            {
                "left": ref("orders", "order_id"),
                "right": ref("payments", "order_id"),
                "pre_aggregate": {
                    "metrics": [{"name": "order_id", "agg": "count"}],
                },
            }
        ],
    )
    assert "different from the join key" in msg


def test_pre_aggregate_metrics_are_validated_too(catalog):
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[COUNT],
        joins=[
            {
                "left": ref("orders", "order_id"),
                "right": ref("payments", "order_id"),
                "pre_aggregate": {
                    "metrics": [{"name": "x", "agg": "sum", "expr": col("payments", "nope")}],
                },
            }
        ],
    )
    assert "Unknown column 'nope' in dataset 'payments'" in msg


def test_sort_keys_must_be_outputs(catalog):
    base = {
        "base_dataset": "orders",
        "metrics": [{"name": "n", "agg": "count"}],
        "dimensions": [ref("orders", "region")],
        "time_bucket": {"column": ref("orders", "order_date"), "grain": "month"},
    }
    validate(spec(**base, sort=[{"by": "n"}, {"by": "region"}, {"by": "time_bucket"}]), catalog)
    for bad in ("order_date", "period", "status", "revenue"):
        msg = rejected(catalog, **base, sort=[{"by": bad}])
        assert f"Cannot sort by '{bad}'" in msg and "time_bucket" in msg


def test_time_bucket_sort_key_needs_a_time_bucket(catalog):
    msg = rejected(catalog, base_dataset="orders", metrics=[COUNT], sort=[{"by": "time_bucket"}])
    assert "Cannot sort by" in msg


def test_metric_name_collisions(catalog):
    dup = rejected(
        catalog,
        base_dataset="orders",
        metrics=[COUNT, {"name": "n", "agg": "sum", "expr": col("orders", "quantity")}],
    )
    assert "unique" in dup and "n" in dup
    with_dim = rejected(
        catalog,
        base_dataset="orders",
        metrics=[{"name": "region", "agg": "count"}],
        dimensions=[ref("orders", "region")],
    )
    assert "region" in with_dim
    with_period = rejected(
        catalog,
        base_dataset="orders",
        metrics=[{"name": "period", "agg": "count"}],
        time_bucket={"column": ref("orders", "order_date"), "grain": "day"},
    )
    assert "period" in with_period


def test_dimensions_with_the_same_column_name_collide(catalog):
    msg = rejected(
        catalog,
        base_dataset="orders",
        metrics=[COUNT],
        dimensions=[ref("orders", "customer_id"), ref("customers", "customer_id")],
        joins=[{"left": ref("orders", "customer_id"), "right": ref("customers", "customer_id")}],
    )
    assert "customer_id" in msg
