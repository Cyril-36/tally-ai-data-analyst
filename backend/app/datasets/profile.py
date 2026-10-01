# SQL below is built only from quoted identifiers; cell values are never SQL.
# ruff: noqa: S608
"""Column profiling. The stored shape (``datasets.profile_json``) is a contract that other
tracks read:

    {"columns": [{"name", "dtype", "role_hint", "null_count", "distinct_count",
                  "sample_values", "min", "max"}],
     "row_count": int, "warnings": [str]}

``dtype`` is the DuckDB type name. ``role_hint`` is one of id | time | dimension | metric.
"""

import math
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

_NUMERIC = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
            "UINTEGER", "UBIGINT", "UHUGEINT", "FLOAT", "DOUBLE", "REAL", "DECIMAL")
_MAX_TEXT = 200  # long cells are truncated in the profile only, never in the data


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def is_numeric(dtype: str) -> bool:
    return dtype.upper().startswith(_NUMERIC)


def is_date_like(dtype: str) -> bool:
    return dtype.upper().startswith(("DATE", "TIMESTAMP"))


def open_parquet(path: Path) -> duckdb.DuckDBPyConnection:
    """In-memory connection with a view ``data`` over one dataset version."""
    con = duckdb.connect()
    con.execute(f"CREATE VIEW data AS SELECT * FROM read_parquet({quote_literal(str(path))})")
    return con


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, str) and len(value) > _MAX_TEXT:
        return value[:_MAX_TEXT] + "..."
    return value


def _is_id_name(name: str) -> bool:
    low = name.lower()
    return low == "id" or low.endswith(("_id", " id", "-id", "_key", "_uuid")) or (
        len(name) > 2 and name.endswith("Id"))


def _role_hint(name: str, dtype: str, row_count: int, non_null: int, distinct: int) -> str:
    if dtype.upper().startswith(("DATE", "TIME")):
        return "time"
    if _is_id_name(name):
        return "id"
    if is_numeric(dtype):
        return "metric"
    # free text that is unique on every row (emails, names, free ids) is not a dimension
    if dtype.upper() == "VARCHAR" and row_count >= 20 and distinct == non_null:
        return "id"
    return "dimension"


def build_profile(con: duckdb.DuckDBPyConnection, table: str, warnings: list[str]) -> dict:
    """Profile ``table`` (a table or view on ``con``)."""
    described = con.execute(f"DESCRIBE {table}").fetchall()
    names = [r[0] for r in described]
    stats = []
    for n in names:
        q = quote_ident(n)
        stats += [f"count({q})", f"count(DISTINCT {q})", f"min({q})", f"max({q})"]
    row = con.execute(f"SELECT count(*), {', '.join(stats)} FROM {table}").fetchone()
    row_count = row[0]

    columns = []
    for i, (name, dtype, *_) in enumerate(described):
        non_null, distinct, lo, hi = row[1 + 4 * i: 5 + 4 * i]
        q = quote_ident(name)
        sample = con.execute(
            f"SELECT DISTINCT {q} FROM {table} WHERE {q} IS NOT NULL ORDER BY {q} LIMIT 5"
        ).fetchall()
        columns.append({
            "name": name,
            "dtype": dtype,
            "role_hint": _role_hint(name, dtype, row_count, non_null, distinct),
            "null_count": row_count - non_null,
            "distinct_count": distinct,
            "sample_values": [jsonable(r[0]) for r in sample],
            "min": jsonable(lo),
            "max": jsonable(hi),
        })
    return {"columns": columns, "row_count": row_count, "warnings": warnings}
