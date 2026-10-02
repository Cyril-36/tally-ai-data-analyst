"""What the analysis engine is allowed to see: only datasets the caller is authorized for.

The caller builds the catalog (name -> DatasetInfo) after its own ownership checks; nothing
in ``app.analysis`` ever queries ownership. A dataset that is missing from the catalog is
indistinguishable from one that does not exist.
"""

from dataclasses import dataclass
from pathlib import Path

from app.contracts.outcomes import AppError, Outcome

_NUMERIC_PREFIXES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT")
_NUMERIC_EXACT = {"FLOAT", "DOUBLE", "REAL", "UINTEGER", "UBIGINT", "USMALLINT", "UHUGEINT"}


@dataclass(frozen=True)
class DatasetInfo:
    id: str
    name: str
    version: int
    parquet_path: Path
    columns: dict[str, str]  # column name -> duckdb dtype
    # Columns profiled as identifiers (role_hint "id"): counting them is ambiguous.
    identifiers: frozenset[str] = frozenset()


Catalog = dict[str, DatasetInfo]


def is_numeric(dtype: str) -> bool:
    d = dtype.upper()
    return d in _NUMERIC_EXACT or d.startswith(_NUMERIC_PREFIXES) or d.startswith("DECIMAL")


def is_temporal(dtype: str) -> bool:
    d = dtype.upper()
    return d == "DATE" or d.startswith("TIMESTAMP")


def is_bool(dtype: str) -> bool:
    return dtype.upper() in ("BOOLEAN", "BOOL")


def lookup_dataset(catalog: Catalog, name: str) -> DatasetInfo:
    if name not in catalog:
        available = ", ".join(sorted(catalog)) or "none"
        raise AppError(
            Outcome.invalid_input, f"Unknown dataset '{name}'. Available datasets: {available}."
        )
    return catalog[name]


def describe_columns(columns: dict[str, str]) -> str:
    return ", ".join(f"{name} ({dtype})" for name, dtype in columns.items()) or "none"


def unknown_column_error(dataset: str, column: str, columns: dict[str, str]) -> AppError:
    return AppError(
        Outcome.invalid_input,
        f"Unknown column '{column}' in dataset '{dataset}'. "
        f"Valid columns: {describe_columns(columns)}.",
    )
