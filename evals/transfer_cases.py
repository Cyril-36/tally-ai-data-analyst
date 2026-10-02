"""Independent holdout schemas: no retail column names or business formulae."""

from cases import Case, col, ref


def metric(dataset, column, agg, name):
    return {"name": name, "agg": agg, "expr": col(dataset, column)}


CASES = [
    Case(
        "distinct_cases",
        "How many distinct case IDs are in support_cases?",
        {"base_dataset": "support_cases", "metrics": [metric(
            "support_cases", "case_id", "count_distinct", "cases"
        )]},
    ),
    Case(
        "resolution_by_team",
        "What is the average resolution_minutes by team in support_cases?",
        {"base_dataset": "support_cases", "dimensions": [ref("support_cases", "team")],
         "metrics": [metric("support_cases", "resolution_minutes", "avg", "mean_minutes")]},
    ),
    Case(
        "monthly_case_count",
        "Show the number of support case rows by month using opened_at.",
        {"base_dataset": "support_cases", "time_bucket": {
            "column": ref("support_cases", "opened_at"), "grain": "month"
        }, "metrics": [{"name": "cases", "agg": "count"}]},
    ),
    Case(
        "fee_by_plan",
        "What is the sum of monthly_fee by plan in subscriptions?",
        {"base_dataset": "subscriptions", "dimensions": [ref("subscriptions", "plan")],
         "metrics": [metric("subscriptions", "monthly_fee", "sum", "total_fee")]},
    ),
    Case(
        "seats_by_plan",
        "What is the average active_seats by plan in subscriptions?",
        {"base_dataset": "subscriptions", "dimensions": [ref("subscriptions", "plan")],
         "metrics": [metric("subscriptions", "active_seats", "avg", "mean_seats")]},
    ),
    Case(
        "monthly_fee_total",
        "Show the sum of monthly_fee by month using started_on in subscriptions.",
        {"base_dataset": "subscriptions", "time_bucket": {
            "column": ref("subscriptions", "started_on"), "grain": "month"
        }, "metrics": [metric("subscriptions", "monthly_fee", "sum", "total_fee")]},
    ),
    Case(
        "support_unsupported",
        "What is the salary of the support team?",
        outcome="unsupported",
    ),
    Case(
        "cases_by_division",
        "How many distinct support cases belong to each division? Join support_cases "
        "to support_teams by team.",
        {"base_dataset": "support_cases", "joins": [{
            "left": ref("support_cases", "team"), "right": ref("support_teams", "team")
        }], "dimensions": [ref("support_teams", "division")],
         "metrics": [metric("support_cases", "case_id", "count_distinct", "cases")]},
    ),
    Case(
        "closed_atlas_cases",
        "How many support case rows are closed and assigned to Atlas?",
        {"base_dataset": "support_cases", "metrics": [{"name": "cases", "agg": "count"}],
         "filters": [
             {"column": ref("support_cases", "status"), "op": "eq", "value": "closed"},
             {"column": ref("support_cases", "team"), "op": "eq", "value": "Atlas"},
         ]},
    ),
    Case(
        "top_seat_plan",
        "Which plan has the highest total active_seats?",
        {"base_dataset": "subscriptions", "dimensions": [ref("subscriptions", "plan")],
         "metrics": [metric("subscriptions", "active_seats", "sum", "seats")],
         "sort": [{"by": "seats", "descending": True}], "limit": 1},
    ),
]
