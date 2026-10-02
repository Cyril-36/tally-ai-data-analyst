"""Second holdout: an unseen logistics schema asked in ordinary business language.

Questions avoid column names and operations ("How many shipments did we send?"), and cover
entity counting (one shipment has two parcel rows), choosing the right date column, missing
values (undelivered shipments), a follow-up, ranking, and an unanswerable question.
"""

from cases import Case, col, ref

D = "shipments"


def distinct_shipments():
    return {"name": "shipments", "agg": "count_distinct", "expr": col(D, "shipment_id")}


CASES = [
    Case(
        "shipments_total",
        "How many shipments did we send?",
        {"base_dataset": D, "metrics": [distinct_shipments()]},
    ),
    Case(
        "costliest_carrier",
        "Which carrier cost us the most in freight?",
        {"base_dataset": D, "dimensions": [ref(D, "carrier")],
         "metrics": [{"name": "freight", "agg": "sum", "expr": col(D, "freight_cost")}],
         "sort": [{"by": "freight", "descending": True}], "limit": 1},
    ),
    Case(
        "shipments_per_month",
        "How many shipments went out each month?",
        {"base_dataset": D, "metrics": [distinct_shipments()],
         "time_bucket": {"column": ref(D, "shipped_on"), "grain": "month"}},
        context_group="monthly_followup",
    ),
    Case(
        "north_followup",
        "Now only for the North destination.",
        {"base_dataset": D, "metrics": [distinct_shipments()],
         "time_bucket": {"column": ref(D, "shipped_on"), "grain": "month"},
         "filters": [{"column": ref(D, "destination"), "op": "eq", "value": "North"}]},
        context_group="monthly_followup",
    ),
    Case(
        "undelivered",
        "How many shipments are still waiting to be delivered?",
        {"base_dataset": D, "metrics": [distinct_shipments()],
         "filters": [{"column": ref(D, "delivered_on"), "op": "is_null"}]},
    ),
    Case(
        "heaviest_destination",
        "Which destination received the most weight?",
        {"base_dataset": D, "dimensions": [ref(D, "destination")],
         "metrics": [{"name": "weight", "agg": "sum", "expr": col(D, "weight_kg")}],
         "sort": [{"by": "weight", "descending": True}], "limit": 1},
    ),
    Case(
        "fuel_price_unsupported",
        "What was the diesel price last week?",
        outcome="unsupported",
    ),
]
