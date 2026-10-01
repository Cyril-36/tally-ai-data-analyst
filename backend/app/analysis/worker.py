"""Isolated query worker: one fresh process per request (PLAN.md section 3, Tier A).

Reads one JSON request on stdin, writes one JSON response on stdout. It imports nothing from
``app`` so it starts fast and has no path to settings or secrets. The parent starts it with an
empty environment and an empty temp dir as cwd, and kills it on timeout.

request:  {"queries": [{"sql", "params"}], "tables": {view: parquet file in cwd},
           "memory_mb": int, "limits": {"RLIMIT_AS": n, ...}}
response: {"ok": true, "results": [{"columns": [[name, type]], "rows": [...]}], "meta": {...}}
       or {"ok": false, "error": str}
"""

import json
import math
import os
import re
import resource
import sys
import time
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal

_VIEW = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
_FILE = re.compile(r"^[A-Za-z0-9_.-]+\.parquet$")


def apply_limits(limits: dict[str, int]) -> dict[str, int]:
    """Set what the OS lets us set; return what actually took effect. macOS rejects
    RLIMIT_AS, for example, so callers must read the result rather than assume."""
    applied = {}
    for name, value in limits.items():
        try:
            resource.setrlimit(getattr(resource, name), (value, value))
            applied[name] = value
        except (AttributeError, ValueError, OSError):
            pass
    return applied


def clean(value):
    """Make a DuckDB value JSON-safe. NaN and infinity become null."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, dtime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    return str(value)


def run(request: dict) -> dict:
    import duckdb

    limits = apply_limits(request["limits"])
    con = duckdb.connect(":memory:")
    for view, filename in request["tables"].items():
        if not _VIEW.match(view) or not _FILE.match(filename):
            raise ValueError("bad table name")
        # view and filename are matched against strict patterns above
        con.execute(f'CREATE TABLE "{view}" AS SELECT * FROM read_parquet(?)', [filename])  # noqa: S608

    # Data is in memory; from here on the connection cannot touch files, the network or
    # extensions, and the settings cannot be changed back.
    con.execute("SET enable_external_access=false")
    con.execute("SET autoinstall_known_extensions=false")
    con.execute("SET autoload_known_extensions=false")
    con.execute(f"SET memory_limit='{int(request['memory_mb'])}MB'")
    con.execute("SET threads=2")
    con.execute("SET lock_configuration=true")

    results = []
    started = time.perf_counter()
    for query in request["queries"]:
        cursor = con.execute(query["sql"], query["params"])
        columns = [[d[0], str(d[1])] for d in cursor.description or []]
        rows = [[clean(v) for v in row] for row in cursor.fetchall()] if columns else []
        results.append({"columns": columns, "rows": rows})
    meta = {
        "limits": limits,
        "env_keys": sorted(os.environ),
        "cwd": os.getcwd(),
        "query_ms": int((time.perf_counter() - started) * 1000),
    }
    return {"ok": True, "results": results, "meta": meta}


def main() -> None:
    try:
        response = run(json.loads(sys.stdin.read()))
    except Exception as exc:
        response = {"ok": False, "error": str(exc) or type(exc).__name__}
    sys.stdout.write(json.dumps(response))


if __name__ == "__main__":
    main()
