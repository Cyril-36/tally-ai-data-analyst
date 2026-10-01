# SQL below is built only from quoted identifiers; cell values are never SQL.
# ruff: noqa: S608
"""Data-quality report for one dataset version. Reports only; it never modifies data.

Checks: nulls per column, exact duplicate rows, candidate keys, numeric-looking text,
inconsistent category spellings, future dates (vs ``reference_date``), negative numbers.

Score (0-100) = 100 minus these capped penalties, floored at 0:
  nulls          min(25, 2 x % of all cells that are null)
  duplicates     min(25, 5 x % of rows that are exact duplicates)
  numeric text   5 per text column that mixes numbers with non-numbers (cap 15)
  variants       5 per column with spelling variants of one value (cap 15)
  future dates   5 per date column with values after the reference date (cap 10)
Candidate keys and negative values are informational and cost nothing.
"""

import re
from datetime import date
from pathlib import Path

from app.datasets.profile import is_date_like, is_numeric, open_parquet, quote_ident

_MAX_VARIANT_DISTINCT = 200  # beyond this a text column is not a category
_NUMERIC_TEXT_SHARE = 0.5


def _issue(level: str, code: str, column: str | None, message: str,
           count: int | None = None) -> dict:
    return {"level": level, "code": code, "column": column, "message": message, "count": count}


def _normalise(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", value.casefold())


def quality_report(parquet_path: Path, profile: dict, reference_date: date) -> dict:
    con = open_parquet(parquet_path)
    n = profile["row_count"]
    cols = profile["columns"]
    issues: list[dict] = []
    penalties = {"nulls": 0.0, "duplicates": 0.0, "numeric_text": 0.0, "variants": 0.0,
                 "future_dates": 0.0}

    # nulls
    null_cells = sum(c["null_count"] for c in cols)
    for c in cols:
        if c["null_count"]:
            pct = round(100 * c["null_count"] / n, 1)
            issues.append(_issue("warning", "nulls", c["name"],
                                 f"{c['null_count']} empty values ({pct}%).", c["null_count"]))
    penalties["nulls"] = min(25.0, 2 * 100 * null_cells / max(n * len(cols), 1))

    # duplicate rows
    distinct_rows = con.execute("SELECT count(*) FROM (SELECT DISTINCT * FROM data)").fetchone()[0]
    duplicates = n - distinct_rows
    if duplicates:
        issues.append(_issue("warning", "duplicate_rows", None,
                             f"{duplicates} rows are exact copies of another row.", duplicates))
    penalties["duplicates"] = min(25.0, 5 * 100 * duplicates / max(n, 1))

    # keys
    candidate_keys = [c["name"] for c in cols
                      if n and c["null_count"] == 0 and c["distinct_count"] == n]
    id_columns = [{"column": c["name"],
                   "unique_pct": round(100 * c["distinct_count"] / max(n - c["null_count"], 1), 1)}
                  for c in cols if c["role_hint"] == "id"]

    numeric_text, variants, future_dates, negatives = [], [], [], []
    for c in cols:
        name, dtype, q = c["name"], c["dtype"].upper(), quote_ident(c["name"])
        non_null = n - c["null_count"]

        if dtype == "VARCHAR" and non_null:
            numeric = con.execute(
                f"SELECT count(*) FILTER (WHERE try_cast(trim({q}) AS DOUBLE) IS NOT NULL) "
                "FROM data"
            ).fetchone()[0]
            if numeric / non_null >= _NUMERIC_TEXT_SHARE:
                bad = [r[0] for r in con.execute(
                    f"SELECT DISTINCT {q} FROM data WHERE {q} IS NOT NULL "
                    f"AND try_cast(trim({q}) AS DOUBLE) IS NULL ORDER BY {q} LIMIT 5").fetchall()]
                numeric_text.append({"column": name, "numeric_count": numeric,
                                     "non_numeric_count": non_null - numeric,
                                     "non_numeric_examples": bad})
                if bad:
                    issues.append(_issue(
                        "warning", "numeric_stored_as_text", name,
                        f"Looks numeric but {non_null - numeric} values are not numbers "
                        f"(e.g. {bad[:3]}).", non_null - numeric))
                else:
                    issues.append(_issue("info", "numeric_stored_as_text", name,
                                         "All values look numeric but are stored as text."))

        if (dtype == "VARCHAR" and c["role_hint"] != "id"
                and 1 < c["distinct_count"] <= _MAX_VARIANT_DISTINCT):
            values = [r[0] for r in con.execute(
                f"SELECT DISTINCT {q} FROM data WHERE {q} IS NOT NULL ORDER BY {q}").fetchall()]
            groups: dict[str, list[str]] = {}
            for v in values:
                if key := _normalise(v):
                    groups.setdefault(key, []).append(v)
            clashes = [g for g in groups.values() if len(g) > 1]
            if clashes:
                variants.append({"column": name, "groups": clashes})
                issues.append(_issue(
                    "warning", "category_variants", name,
                    f"{len(clashes)} value(s) are spelled in more than one way, "
                    f"e.g. {clashes[0]}.", len(clashes)))

        if is_date_like(dtype) and non_null:
            future = con.execute(
                f"SELECT count(*) FROM data WHERE {q} > DATE '{reference_date.isoformat()}'"
            ).fetchone()[0]
            if future:
                future_dates.append({"column": name, "count": future})
                issues.append(_issue("warning", "future_dates", name,
                                     f"{future} dates are after {reference_date.isoformat()}.",
                                     future))

        if is_numeric(dtype) and non_null:
            negative = con.execute(f"SELECT count(*) FROM data WHERE {q} < 0").fetchone()[0]
            if negative:
                negatives.append({"column": name, "count": negative})
                issues.append(_issue("info", "negative_values", name,
                                     f"{negative} negative values.", negative))

    penalties["numeric_text"] = min(15.0, 5.0 * sum(1 for t in numeric_text
                                                    if t["non_numeric_count"]))
    penalties["variants"] = min(15.0, 5.0 * len(variants))
    penalties["future_dates"] = min(10.0, 5.0 * len(future_dates))
    score = max(0, round(100 - sum(penalties.values())))

    return {
        "score": score,
        "penalties": {k: round(v, 1) for k, v in penalties.items()},
        "row_count": n,
        "reference_date": reference_date.isoformat(),
        "columns": [{"name": c["name"], "null_count": c["null_count"],
                     "null_pct": round(100 * c["null_count"] / n, 1) if n else 0.0}
                    for c in cols],
        "duplicate_rows": duplicates,
        "candidate_keys": candidate_keys,
        "id_columns": id_columns,
        "numeric_text_columns": numeric_text,
        "category_variants": variants,
        "future_dates": future_dates,
        "negative_values": negatives,
        "issues": issues,
    }
