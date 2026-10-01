"""CSV upload -> Parquet. Cell text is data: it is parsed by DuckDB's CSV reader and stored,
never interpreted, executed or fed to any prompt here.

Every refusal is ``AppError(Outcome.invalid_input, ...)`` naming the file and the problem.
"""

import csv
import tempfile
from pathlib import Path

import duckdb

from app.contracts.outcomes import AppError, Outcome
from app.datasets.profile import build_profile, quote_literal

_DELIMITERS = [",", ";", "\t", "|"]


def _refuse(filename: str, problem: str) -> AppError:
    return AppError(Outcome.invalid_input, f"'{filename}': {problem}")


def _decode(data: bytes, filename: str, warnings: list[str]) -> str:
    if b"\x00" in data:
        raise _refuse(filename, "this does not look like a text CSV file (binary data).")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        warnings.append("The file is not valid UTF-8, so it was read as latin-1. "
                        "Some accented characters may look wrong.")
        return data.decode("latin-1")


def _header(text: str, warnings: list[str]) -> tuple[str, list[str]]:
    """Sniff the delimiter from the first line and return clean, unique column names."""
    first = next(line for line in text.splitlines() if line.strip())
    best, fields = ",", next(csv.reader([first], delimiter=","))
    for d in _DELIMITERS[1:]:
        candidate = next(csv.reader([first], delimiter=d))
        if len(candidate) > len(fields):
            best, fields = d, candidate

    names: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(fields, 1):
        base = raw.strip() or f"column_{i}"
        name, k = base, 1
        while name.lower() in seen:  # DuckDB column names are case-insensitive
            k += 1
            name = f"{base}_{k}"
        if name != base:
            warnings.append(f"Duplicate column name '{base}' was renamed to '{name}'.")
        seen.add(name.lower())
        names.append(name)
    return best, names


def ingest_csv(data: bytes, filename: str, parquet_path: Path) -> dict:
    """Validate ``data``, write it to ``parquet_path`` and return the profile dict.
    Nothing is written if the file is refused."""
    if not filename.lower().endswith(".csv"):
        raise _refuse(filename, "only .csv files are accepted.")

    warnings: list[str] = []
    text = _decode(data, filename, warnings).lstrip()
    if not text:
        raise _refuse(filename, "the file is empty.")
    delimiter, names = _header(text, warnings)

    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "upload.csv"
        clean.write_text(text, encoding="utf-8", newline="")
        con = duckdb.connect()
        try:
            con.execute(
                "CREATE TABLE data AS SELECT * FROM read_csv(?, delim=?, header=true, names=?, "
                "sample_size=-1, ignore_errors=true, store_rejects=true)",
                [str(clean), delimiter, names],
            )
            rows = con.execute("SELECT count(*) FROM data").fetchone()[0]
            rejected = [r[0] for r in con.execute(
                "SELECT DISTINCT line FROM reject_errors ORDER BY line").fetchall()]
        except duckdb.Error as e:
            raise _refuse(filename, "it could not be parsed as a CSV file.") from e

        if rows == 0:
            data_lines = [line for line in text.splitlines()[1:] if line.strip()]
            if not data_lines:
                raise _refuse(filename, "the file has a header row but no data rows.")
            raise _refuse(filename, "none of the data rows could be parsed "
                                    "(check that every row has the same number of columns).")
        if rejected:
            shown = ", ".join(str(n) for n in rejected[:5])
            more = "..." if len(rejected) > 5 else ""
            warnings.append(f"{len(rejected)} malformed row(s) were skipped "
                            f"(wrong number of columns; file lines {shown}{more}).")

        profile = build_profile(con, "data", warnings)
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY data TO {quote_literal(str(parquet_path))} (FORMAT parquet)")
    return profile
