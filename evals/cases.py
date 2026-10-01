"""Frozen natural-language evaluation cases and independently specified expected analyses."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    spec: dict | None = None
    outcome: str = "ok"
    anomaly_metric: str | None = None
    context_group: str | None = None


def ref(dataset: str, column: str) -> dict:
    return {"dataset": dataset, "column": column}


def col(dataset: str, column: str) -> dict:
    return {"kind": "col", "ref": ref(dataset, column)}


REVENUE = {
    "kind": "binop",
    "op": "-",
    "left": {
        "kind": "binop",
        "op": "*",
        "left": col("orders", "quantity"),
        "right": col("orders", "unit_price"),
    },
    "right": col("orders", "discount_amount"),
}
METRICS = {
    "revenue": {"name": "revenue", "agg": "sum", "expr": REVENUE},
    "orders": {"name": "orders", "agg": "count"},
    "units": {"name": "units", "agg": "sum", "expr": col("orders", "quantity")},
    "avg_units": {"name": "avg_units", "agg": "avg", "expr": col("orders", "quantity")},
    "customers": {
        "name": "customers",
        "agg": "count_distinct",
        "expr": col("orders", "customer_id"),
    },
    "avg_order_revenue": {"name": "avg_order_revenue", "agg": "avg", "expr": REVENUE},
}
CUSTOMER_JOIN = {"left": ref("orders", "customer_id"), "right": ref("customers", "customer_id")}
PRODUCT_JOIN = {"left": ref("orders", "product_id"), "right": ref("products", "product_id")}
MONTH = {"column": ref("orders", "order_date"), "grain": "month"}
QUARTER = {"column": ref("orders", "order_date"), "grain": "quarter"}
YEAR_2026 = {
    "column": ref("orders", "order_date"),
    "op": "between",
    "value": ["2026-01-01", "2026-12-31"],
}


def spec(
    metric: str,
    *,
    dimensions: list[dict] | None = None,
    time_bucket: dict | None = None,
    filters: list[dict] | None = None,
    joins: list[dict] | None = None,
    sort: list[dict] | None = None,
    limit: int | None = None,
) -> dict:
    result = {"base_dataset": "orders", "metrics": [METRICS[metric]]}
    if dimensions:
        result["dimensions"] = dimensions
    if time_bucket:
        result["time_bucket"] = time_bucket
    if filters:
        result["filters"] = filters
    if joins:
        result["joins"] = joins
    if sort:
        result["sort"] = sort
    if limit:
        result["limit"] = limit
    return result


REGION = [ref("orders", "region")]
REVENUE_DESC = [{"by": "revenue", "descending": True}]
REVENUE_ASC = [{"by": "revenue", "descending": False}]
WEST = {"column": ref("orders", "region"), "op": "eq", "value": "West"}
EAST = {"column": ref("orders", "region"), "op": "eq", "value": "East"}

CASES = [
    Case("total_revenue", "What is total revenue?", spec("revenue")),
    Case("revenue_region", "Show revenue for each region.", spec("revenue", dimensions=REGION)),
    Case(
        "highest_region",
        "Which region generated the highest revenue?",
        spec("revenue", dimensions=REGION, sort=REVENUE_DESC, limit=1),
    ),
    Case(
        "lowest_region",
        "Which region generated the lowest revenue?",
        spec("revenue", dimensions=REGION, sort=REVENUE_ASC, limit=1),
    ),
    Case("orders_total", "How many orders are there?", spec("orders")),
    Case(
        "orders_region", "How many orders came from each region?", spec("orders", dimensions=REGION)
    ),
    Case("units_total", "How many units were sold in total?", spec("units")),
    Case(
        "units_region", "How many units were sold in each region?", spec("units", dimensions=REGION)
    ),
    Case("avg_units", "What is the average number of units per order?", spec("avg_units")),
    Case("unique_customers", "How many distinct customers placed orders?", spec("customers")),
    Case("avg_order_revenue", "What is average revenue per order?", spec("avg_order_revenue")),
    Case(
        "monthly_revenue", "Show monthly sales revenue trends.", spec("revenue", time_bucket=MONTH)
    ),
    Case("monthly_orders", "Show the monthly order count.", spec("orders", time_bucket=MONTH)),
    Case("monthly_units", "Show units sold by month.", spec("units", time_bucket=MONTH)),
    Case("quarterly_revenue", "Show revenue by quarter.", spec("revenue", time_bucket=QUARTER)),
    Case(
        "west_monthly",
        "Show monthly revenue for West.",
        spec("revenue", time_bucket=MONTH, filters=[WEST]),
        context_group="region_followup",
    ),
    Case(
        "east_followup",
        "Now only East.",
        spec("revenue", time_bucket=MONTH, filters=[EAST]),
        context_group="region_followup",
    ),
    Case("west_revenue", "What is total revenue from West?", spec("revenue", filters=[WEST])),
    Case(
        "revenue_2026",
        "What was total revenue in calendar year 2026?",
        spec("revenue", filters=[YEAR_2026]),
    ),
    Case(
        "monthly_2026",
        "Show monthly revenue during calendar year 2026.",
        spec("revenue", time_bucket=MONTH, filters=[YEAR_2026]),
    ),
    Case(
        "top_customers",
        "Who are the top five customers by revenue?",
        spec(
            "revenue",
            dimensions=[ref("customers", "customer_name")],
            joins=[CUSTOMER_JOIN],
            sort=REVENUE_DESC,
            limit=5,
        ),
    ),
    Case(
        "customer_segment",
        "Show revenue by customer segment.",
        spec("revenue", dimensions=[ref("customers", "segment")], joins=[CUSTOMER_JOIN]),
    ),
    Case(
        "revenue_product",
        "Show revenue by product name.",
        spec("revenue", dimensions=[ref("products", "product_name")], joins=[PRODUCT_JOIN]),
    ),
    Case(
        "revenue_category",
        "Show revenue by product category.",
        spec("revenue", dimensions=[ref("products", "category")], joins=[PRODUCT_JOIN]),
    ),
    Case(
        "top_products",
        "Show the top five products by revenue.",
        spec(
            "revenue",
            dimensions=[ref("products", "product_name")],
            joins=[PRODUCT_JOIN],
            sort=REVENUE_DESC,
            limit=5,
        ),
    ),
    Case(
        "underperforming_ambiguous",
        "Which products are underperforming?",
        outcome="needs_clarification",
    ),
    Case("best_ambiguous", "Which region performed best?", outcome="needs_clarification"),
    Case("unsupported_employees", "What is our employee attrition rate?", outcome="unsupported"),
    Case(
        "sql_request",
        "Generate the SQL to calculate revenue by region.",
        spec("revenue", dimensions=REGION),
    ),
    Case(
        "monthly_anomalies",
        "Detect anomalies in monthly revenue by region.",
        spec("revenue", dimensions=REGION, time_bucket=MONTH),
        anomaly_metric="revenue",
    ),
]
