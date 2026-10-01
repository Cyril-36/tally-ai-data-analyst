"""Shared fixtures for the analysis tests (helpers only, no tests)."""

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from app.analysis.catalog import Catalog, DatasetInfo
from app.contracts.spec import AnalysisSpec


def make_catalog(tmp_path: Path, frames: dict[str, pd.DataFrame]) -> Catalog:
    catalog: Catalog = {}
    for i, (name, frame) in enumerate(frames.items()):
        path = tmp_path / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        described = duckdb.connect().execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)])
        columns = {row[0]: row[1] for row in described.fetchall()}
        catalog[name] = DatasetInfo(
            id=f"id{i}", name=name, version=1, parquet_path=path, columns=columns
        )
    return catalog


def sample_frames() -> dict[str, pd.DataFrame]:
    orders = pd.DataFrame(
        {
            "order_id": [1, 2, 3, 4, 5, 6],
            "customer_id": [10, 10, 11, 12, 99, 11],
            "product_id": [1, 2, 1, 3, 2, 3],
            "region": ["West", "East", "West", "North", "East", None],
            "order_date": [
                date(2026, 1, 5),
                date(2026, 1, 20),
                date(2026, 2, 3),
                date(2026, 2, 14),
                date(2026, 3, 1),
                date(2026, 3, 9),
            ],
            "quantity": [2, 1, 3, 1, 4, 2],
            "unit_price": [50.0, 100.0, 50.0, 30.0, 100.0, None],
            "discount_amount": [0.0, 10.0, 0.0, 0.0, 20.0, 0.0],
            "status": ["paid", "paid", "refunded", "paid", "paid", "paid"],
        }
    )
    customers = pd.DataFrame(
        {
            "customer_id": [10, 11, 12],
            "customer_name": ["Ada Lovelace", "Grace Hopper", "Alan Turing"],
            "segment": ["retail", "retail", "wholesale"],
            "signup_at": pd.to_datetime(["2025-06-01 09:30:00", "2025-07-15 12:00:00", None]),
        }
    )
    payments = pd.DataFrame(
        {
            "payment_id": [1, 2, 3, 4],
            "order_id": [1, 1, 2, 3],
            "amount": [50.0, 50.0, 90.0, 150.0],
        }
    )
    products = pd.DataFrame(
        {
            "product_id": [1, 2, 3],
            "product_name": ["Widget", "Gadget", "Gizmo"],
            "category": ["tools", "tools", "toys"],
        }
    )
    dup_dim = pd.DataFrame(
        {"customer_id": [10, 10, 11], "tier": ["gold", "silver", "gold"]},
    )
    return {
        "orders": orders,
        "customers": customers,
        "payments": payments,
        "products": products,
        "dup_dim": dup_dim,
    }


def spec(**fields) -> AnalysisSpec:
    """Build a spec from plain dicts, the way the planner's JSON would arrive."""
    return AnalysisSpec.model_validate(fields)


def ref(dataset: str, column: str) -> dict:
    return {"dataset": dataset, "column": column}


def col(dataset: str, column: str) -> dict:
    return {"kind": "col", "ref": ref(dataset, column)}


def revenue_expr() -> dict:
    """quantity * unit_price - discount_amount (the documented revenue definition)."""
    return {
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
