"""Golden tests: the exact SQL and parameters for representative specs."""

# ruff: noqa: E501  (golden SQL strings are long on purpose)

import pytest

from app.analysis.compiler import compile_spec
from app.analysis.validator import validate
from app.config import get_settings
from app.contracts.result import ColumnRole
from tests.test_analysis_data import (
    col,
    make_catalog,
    ref,
    revenue_expr,
    sample_frames,
    spec,
)

REVENUE = {"name": "revenue", "agg": "sum", "expr": revenue_expr()}
BY_CUSTOMER_JOIN = {"left": ref("orders", "customer_id"), "right": ref("customers", "customer_id")}

CASES = {
    # "Which region has the highest revenue?"
    "highest_revenue_region": dict(
        base_dataset="orders",
        metrics=[REVENUE],
        dimensions=[ref("orders", "region")],
        sort=[{"by": "revenue", "descending": True}],
        limit=1,
    ),
    # "What is the monthly revenue trend?"
    "monthly_trend": dict(
        base_dataset="orders",
        metrics=[REVENUE],
        time_bucket={"column": ref("orders", "order_date"), "grain": "month"},
    ),
    "monthly_trend_by_region": dict(
        base_dataset="orders",
        metrics=[REVENUE],
        dimensions=[ref("orders", "region")],
        time_bucket={"column": ref("orders", "order_date"), "grain": "quarter"},
    ),
    # "Who are the top 5 customers by revenue?"
    "top_customers_via_join": dict(
        base_dataset="orders",
        metrics=[REVENUE],
        dimensions=[ref("customers", "customer_name")],
        joins=[BY_CUSTOMER_JOIN],
        limit=5,
    ),
    # "Which products are underperforming?"
    "underperforming_products": dict(
        base_dataset="orders",
        metrics=[
            REVENUE,
            {"name": "units", "agg": "sum", "expr": col("orders", "quantity")},
        ],
        dimensions=[ref("products", "product_name")],
        joins=[{"left": ref("orders", "product_id"), "right": ref("products", "product_id")}],
        sort=[{"by": "revenue", "descending": False}],
        limit=5,
    ),
    "filters_in_between_null": dict(
        base_dataset="orders",
        metrics=[{"name": "orders", "agg": "count"}],
        dimensions=[ref("orders", "status")],
        filters=[
            {"column": ref("orders", "region"), "op": "in", "value": ["West", "East"]},
            {
                "column": ref("orders", "order_date"),
                "op": "between",
                "value": ["2026-01-01", "2026-03-31"],
            },
            {"column": ref("orders", "unit_price"), "op": "not_null"},
            {"column": ref("orders", "discount_amount"), "op": "is_null"},
            {"column": ref("orders", "status"), "op": "ne", "value": "refunded"},
        ],
    ),
    "comparison_filters": dict(
        base_dataset="orders",
        metrics=[{"name": "orders", "agg": "count"}],
        filters=[
            {"column": ref("orders", "quantity"), "op": "gte", "value": 2},
            {"column": ref("orders", "unit_price"), "op": "lt", "value": 99.5},
            {"column": ref("orders", "region"), "op": "not_in", "value": ["North"]},
            {"column": ref("orders", "order_date"), "op": "gt", "value": "2026-01-31"},
        ],
    ),
    "aggregates": dict(
        base_dataset="orders",
        metrics=[
            {"name": "rows", "agg": "count"},
            {"name": "customers", "agg": "count_distinct", "expr": col("orders", "customer_id")},
            {"name": "median_price", "agg": "median", "expr": col("orders", "unit_price")},
            {"name": "avg_price", "agg": "avg", "expr": col("orders", "unit_price")},
        ],
    ),
    "min_max": dict(
        base_dataset="orders",
        metrics=[
            {"name": "max_qty", "agg": "max", "expr": col("orders", "quantity")},
            {"name": "min_qty", "agg": "min", "expr": col("orders", "quantity")},
        ],
        dimensions=[ref("orders", "region"), ref("orders", "status")],
        sort=[{"by": "max_qty"}, {"by": "region", "descending": False}],
    ),
    "division_is_nullif": dict(
        base_dataset="orders",
        metrics=[
            {
                "name": "price_per_unit",
                "agg": "avg",
                "expr": {
                    "kind": "binop",
                    "op": "/",
                    "left": {
                        "kind": "binop",
                        "op": "-",
                        "left": col("orders", "unit_price"),
                        "right": col("orders", "discount_amount"),
                    },
                    "right": {
                        "kind": "binop",
                        "op": "+",
                        "left": col("orders", "quantity"),
                        "right": {"kind": "num", "value": 0.5},
                    },
                },
            }
        ],
        dimensions=[ref("orders", "region")],
    ),
    "pre_aggregated_join": dict(
        base_dataset="orders",
        metrics=[{"name": "paid", "agg": "sum", "expr": col("payments", "paid")}],
        dimensions=[ref("orders", "region")],
        joins=[
            {
                "left": ref("orders", "order_id"),
                "right": ref("payments", "order_id"),
                "pre_aggregate": {
                    "metrics": [{"name": "paid", "agg": "sum", "expr": col("payments", "amount")}]
                },
            }
        ],
    ),
    "timestamp_column": dict(
        base_dataset="customers",
        metrics=[{"name": "signups", "agg": "count"}],
        time_bucket={"column": ref("customers", "signup_at"), "grain": "year"},
        filters=[
            {
                "column": ref("customers", "signup_at"),
                "op": "gte",
                "value": "2025-06-15 00:00:00",
            }
        ],
    ),
}

GOLDEN = {
    "highest_revenue_region": {
        "sql": (
            'SELECT "t0"."region" AS "region", SUM(("t0"."quantity" * "t0"."unit_price") - "t0"."discount_amount") AS "revenue" '
            'FROM "t0" '
            'GROUP BY "t0"."region" '
            'ORDER BY "revenue" DESC, "region" ASC '
            "LIMIT 2"
        ),
        "params": [],
    },
    "monthly_trend": {
        "sql": (
            'SELECT CAST(DATE_TRUNC(\'month\', "t0"."order_date") AS DATE) AS "period", SUM(("t0"."quantity" * "t0"."unit_price") - "t0"."discount_amount") AS "revenue" '
            'FROM "t0" '
            'GROUP BY CAST(DATE_TRUNC(\'month\', "t0"."order_date") AS DATE) '
            'ORDER BY "period" ASC '
            "LIMIT 1001"
        ),
        "params": [],
    },
    "monthly_trend_by_region": {
        "sql": (
            'SELECT CAST(DATE_TRUNC(\'quarter\', "t0"."order_date") AS DATE) AS "period", "t0"."region" AS "region", SUM(("t0"."quantity" * "t0"."unit_price") - "t0"."discount_amount") AS "revenue" '
            'FROM "t0" '
            'GROUP BY CAST(DATE_TRUNC(\'quarter\', "t0"."order_date") AS DATE), "t0"."region" '
            'ORDER BY "period" ASC, "region" ASC '
            "LIMIT 1001"
        ),
        "params": [],
    },
    "top_customers_via_join": {
        "sql": (
            'SELECT "t1"."customer_name" AS "customer_name", SUM(("t0"."quantity" * "t0"."unit_price") - "t0"."discount_amount") AS "revenue" '
            'FROM "t0" '
            'LEFT JOIN "t1" ON "t0"."customer_id" = "t1"."customer_id" '
            'GROUP BY "t1"."customer_name" '
            'ORDER BY "revenue" DESC, "customer_name" ASC '
            "LIMIT 6"
        ),
        "params": [],
    },
    "underperforming_products": {
        "sql": (
            'SELECT "t1"."product_name" AS "product_name", SUM(("t0"."quantity" * "t0"."unit_price") - "t0"."discount_amount") AS "revenue", SUM("t0"."quantity") AS "units" '
            'FROM "t0" '
            'LEFT JOIN "t1" ON "t0"."product_id" = "t1"."product_id" '
            'GROUP BY "t1"."product_name" '
            'ORDER BY "revenue" ASC, "product_name" ASC '
            "LIMIT 6"
        ),
        "params": [],
    },
    "filters_in_between_null": {
        "sql": (
            'SELECT "t0"."status" AS "status", COUNT(*) AS "orders" '
            'FROM "t0" '
            'WHERE "t0"."region" IN (?, ?) AND "t0"."order_date" BETWEEN CAST(? AS DATE) AND CAST(? AS DATE) AND NOT "t0"."unit_price" IS NULL AND "t0"."discount_amount" IS NULL AND "t0"."status" <> ? '
            'GROUP BY "t0"."status" '
            'ORDER BY "orders" DESC, "status" ASC '
            "LIMIT 1001"
        ),
        "params": ["West", "East", "2026-01-01", "2026-03-31", "refunded"],
    },
    "comparison_filters": {
        "sql": (
            'SELECT COUNT(*) AS "orders" '
            'FROM "t0" '
            'WHERE "t0"."quantity" >= ? AND "t0"."unit_price" < ? AND NOT "t0"."region" IN (?) AND "t0"."order_date" > CAST(? AS DATE) '
            "LIMIT 1001"
        ),
        "params": [2, 99.5, "North", "2026-01-31"],
    },
    "aggregates": {
        "sql": (
            'SELECT COUNT(*) AS "rows", COUNT(DISTINCT "t0"."customer_id") AS "customers", MEDIAN("t0"."unit_price") AS "median_price", AVG("t0"."unit_price") AS "avg_price" '
            'FROM "t0" '
            "LIMIT 1001"
        ),
        "params": [],
    },
    "min_max": {
        "sql": (
            'SELECT "t0"."region" AS "region", "t0"."status" AS "status", MAX("t0"."quantity") AS "max_qty", MIN("t0"."quantity") AS "min_qty" '
            'FROM "t0" '
            'GROUP BY "t0"."region", "t0"."status" '
            'ORDER BY "max_qty" DESC, "region" ASC, "status" ASC '
            "LIMIT 1001"
        ),
        "params": [],
    },
    "division_is_nullif": {
        "sql": (
            'SELECT "t0"."region" AS "region", AVG(("t0"."unit_price" - "t0"."discount_amount") / NULLIF("t0"."quantity" + 0.5, 0)) AS "price_per_unit" '
            'FROM "t0" '
            'GROUP BY "t0"."region" '
            'ORDER BY "price_per_unit" DESC, "region" ASC '
            "LIMIT 1001"
        ),
        "params": [],
    },
    "pre_aggregated_join": {
        "sql": (
            'SELECT "t0"."region" AS "region", SUM("p1"."paid") AS "paid" '
            'FROM "t0" '
            'LEFT JOIN (SELECT "t1"."order_id" AS "order_id", SUM("t1"."amount") AS "paid" '
            'FROM "t1" '
            'GROUP BY "t1"."order_id") AS "p1" ON "t0"."order_id" = "p1"."order_id" '
            'GROUP BY "t0"."region" '
            'ORDER BY "paid" DESC, "region" ASC '
            "LIMIT 1001"
        ),
        "params": [],
    },
    "timestamp_column": {
        "sql": (
            'SELECT DATE_TRUNC(\'year\', "t0"."signup_at") AS "period", COUNT(*) AS "signups" '
            'FROM "t0" '
            'WHERE "t0"."signup_at" >= CAST(? AS TIMESTAMP) '
            'GROUP BY DATE_TRUNC(\'year\', "t0"."signup_at") '
            'ORDER BY "period" ASC '
            "LIMIT 1001"
        ),
        "params": ["2025-06-15 00:00:00"],
    },
}


@pytest.fixture
def catalog(tmp_path):
    return make_catalog(tmp_path, sample_frames())


@pytest.mark.parametrize("name", list(CASES))
def test_golden_sql(name, catalog):
    s = spec(**CASES[name])
    validate(s, catalog)
    compiled = compile_spec(s, catalog)
    assert compiled.sql == GOLDEN[name]["sql"]
    assert compiled.params == GOLDEN[name]["params"]


def test_tables_and_roles(catalog):
    compiled = compile_spec(spec(**CASES["monthly_trend_by_region"]), catalog)
    assert compiled.tables == {"t0": catalog["orders"].parquet_path}
    assert compiled.result_roles == {
        "period": ColumnRole.time,
        "region": ColumnRole.dimension,
        "revenue": ColumnRole.metric,
    }
    joined = compile_spec(spec(**CASES["top_customers_via_join"]), catalog)
    assert joined.tables == {
        "t0": catalog["orders"].parquet_path,
        "t1": catalog["customers"].parquet_path,
    }


def test_values_are_parameters_not_sql_text(catalog):
    hostile = "x'; DROP TABLE orders; --"
    s = spec(
        base_dataset="orders",
        metrics=[{"name": "n", "agg": "count"}],
        filters=[{"column": ref("orders", "region"), "op": "eq", "value": hostile}],
    )
    compiled = compile_spec(s, catalog)
    assert "DROP" not in compiled.sql
    assert compiled.params == [hostile]
    assert compiled.sql.count("?") == 1


def test_identifiers_are_quoted_and_escaped(tmp_path):
    import pandas as pd

    frame = pd.DataFrame({'we"ird col': [1, 2, 3], "select": ["a", "b", "c"]})
    catalog = make_catalog(tmp_path, {"data": frame})
    s = spec(
        base_dataset="data",
        metrics=[{"name": "total", "agg": "sum", "expr": col("data", 'we"ird col')}],
        dimensions=[ref("data", "select")],
    )
    sql = compile_spec(s, catalog).sql
    assert '"we""ird col"' in sql
    assert 'SELECT "data"' not in sql
    assert '"t0"."select" AS "select"' in sql


def test_limit_is_capped_and_fetches_one_extra(catalog, monkeypatch):
    monkeypatch.setattr(get_settings(), "result_row_cap", 3)
    capped = compile_spec(spec(**{**CASES["monthly_trend"], "limit": 50}), catalog)
    assert (capped.limit, capped.capped) == (3, True)
    assert capped.sql.endswith("LIMIT 4")
    unset = compile_spec(spec(**CASES["monthly_trend"]), catalog)
    assert (unset.limit, unset.capped) == (3, True)
    explicit = compile_spec(spec(**CASES["highest_revenue_region"]), catalog)
    assert (explicit.limit, explicit.capped) == (1, False)
    assert explicit.sql.endswith("LIMIT 2")


def test_null_check_covers_metric_inputs_not_preaggregated_columns(catalog):
    compiled = compile_spec(spec(**CASES["highest_revenue_region"]), catalog)
    assert compiled.null_check_inputs == [
        "orders.quantity",
        "orders.unit_price",
        "orders.discount_amount",
    ]
    assert compile_spec(spec(**CASES["pre_aggregated_join"]), catalog).null_check_sql is None
    assert compile_spec(spec(**CASES["aggregates"]), catalog).null_check_inputs == [
        "orders.customer_id",
        "orders.unit_price",
    ]
