"""Scripted stand-in for the LLM, selected only by ``LLM_PROVIDER=fake`` (E2E and demos).

It plays the model's part in the REAL agent loop: it returns deterministic tool calls for a
fixed set of demo questions, so specs are validated, compiled, executed and composed by the
normal code. Every number the user sees still comes from the tools, never from here.
The demo questions use the sample datasets by name (orders, customers).
"""

import json

from app.agent.provider import Provider, Reply, ToolCall
from app.contracts.spec import (
    AnalysisSpec,
    BinOp,
    Col,
    ColumnRef,
    Join,
    Metric,
    SortKey,
    TimeBucket,
    TimeGrain,
)


def _orders(column: str) -> Col:
    return Col(ref=ColumnRef(dataset="orders", column=column))


# revenue = quantity * unit_price - discount_amount
_REVENUE = Metric(
    name="revenue",
    agg="sum",
    expr=BinOp(
        op="-",
        left=BinOp(op="*", left=_orders("quantity"), right=_orders("unit_price")),
        right=_orders("discount_amount"),
    ),
)
_REGION = ColumnRef(dataset="orders", column="region")
_ORDER_DATE = TimeBucket(column=ColumnRef(dataset="orders", column="order_date"),
                         grain=TimeGrain.month)
_CUSTOMER_JOIN = Join(left=ColumnRef(dataset="orders", column="customer_id"),
                      right=ColumnRef(dataset="customers", column="customer_id"))

_PRODUCT_JOIN = Join(left=ColumnRef(dataset="orders", column="product_id"),
                     right=ColumnRef(dataset="products", column="product_id"))

_SPECS = {
    "region": AnalysisSpec(
        base_dataset="orders", metrics=[_REVENUE], dimensions=[_REGION],
        sort=[SortKey(by="revenue", descending=True)]),
    "monthly": AnalysisSpec(
        base_dataset="orders", metrics=[_REVENUE], time_bucket=_ORDER_DATE,
        sort=[SortKey(by="time_bucket", descending=False)]),
    "customers": AnalysisSpec(
        base_dataset="orders", metrics=[_REVENUE], joins=[_CUSTOMER_JOIN],
        dimensions=[ColumnRef(dataset="customers", column="customer_id"),
                    ColumnRef(dataset="customers", column="customer_name")],
        sort=[SortKey(by="revenue", descending=True)], limit=5),
    # The follow-up to the "underperforming" clarification when the user picks revenue.
    "products": AnalysisSpec(
        base_dataset="orders", metrics=[_REVENUE], joins=[_PRODUCT_JOIN],
        dimensions=[ColumnRef(dataset="products", column="product_name")],
        sort=[SortKey(by="revenue", descending=False)], limit=5),
    # Per-region monthly series, so each region is judged against its own history.
    "anomalies": AnalysisSpec(
        base_dataset="orders", metrics=[_REVENUE], dimensions=[_REGION], time_bucket=_ORDER_DATE,
        sort=[SortKey(by="time_bucket", descending=False)]),
}
_PLANS = {
    "region": ["run_analysis", "finish"],
    "monthly": ["run_analysis", "finish"],
    "customers": ["check_join", "run_analysis", "finish"],
    "products": ["check_join", "run_analysis", "finish"],
    "anomalies": ["run_analysis", "detect_anomalies"],
}
_NEEDS = {"customers": {"orders", "customers"}, "products": {"orders", "products"}}
# Topics the sample data cannot answer; a real model would decline with `unsupported`.
_NOT_IN_DATA = ("employee", "attrition", "headcount", "salary", "weather")


def _intent(question: str) -> str | None:
    q = question.casefold()
    if "underperform" in q:
        return "underperform"
    if any(word in q for word in _NOT_IN_DATA):
        return "not_in_data"
    if "anomal" in q:
        return "anomalies"
    if q.strip() == "revenue" or ("product" in q and "revenue" in q):
        return "products"
    if "region" in q and "revenue" in q:
        return "region"
    if "customer" in q and ("top" in q or "best" in q):
        return "customers"
    if "monthly" in q and ("sales" in q or "revenue" in q or "trend" in q):
        return "monthly"
    return None


def _call(name: str, args: dict, step: int) -> list[ToolCall]:
    return [ToolCall(f"fake-{step}", name, args)]


class ScriptedProvider(Provider):
    def ping(self) -> str:
        return "scripted-fake"

    def complete(self, messages: list[dict], remaining_s: float, max_tokens: int) -> Reply:
        request = json.loads(messages[1]["content"])
        datasets = {d["name"] for d in request["context"]["datasets"]}
        observed = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        step = len(observed)
        intent = _intent(request["question"])

        if intent == "underperform":
            args = {"reason": "metric", "options": ["revenue", "profit", "declining sales"]}
            return Reply(_call("clarify", args, step))
        if intent == "not_in_data":
            return Reply(_call("unsupported", {"reason": "missing_data"}, step))
        if intent is None:  # the fixed clarify text asks which metric to use
            return Reply(_call("clarify", {"reason": "metric", "options": ["revenue"]}, step))
        if not {"orders", *_NEEDS.get(intent, ())} <= datasets:
            options = sorted(datasets)
            return Reply(_call("clarify", {"reason": "dataset", "options": options}, step))
        # Anything unexpected from a tool (an error observation, a refused join) ends the turn.
        if any("outcome" in o or o.get("allowed") is False for o in observed):
            return Reply(_call("unsupported", {"reason": "operation"}, step))

        name = _PLANS[intent][step]
        if name == "check_join":
            args = {"join_json": _CUSTOMER_JOIN.model_dump_json()}
        elif name == "run_analysis":
            args = {"spec_json": _SPECS[intent].model_dump_json()}
        elif name == "detect_anomalies":
            args = {"result_id": observed[-1]["result_id"], "metric": "revenue"}
        else:
            args = {"result_id": observed[-1]["result_id"]}
        return Reply(_call(name, args, step))
