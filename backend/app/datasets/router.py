"""Dataset endpoints. Every by-id access goes through ``authz.require_owned``, so another
user's dataset is indistinguishable from a missing one (404).

Handlers are plain ``def`` (run in the threadpool) so they can use the sqlite connection and
the upload's sync file object directly.
"""

import json
import shutil
import sqlite3
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile

from app.admin.router import read_system_settings
from app.auth import authz
from app.auth.authz import Action
from app.auth.deps import User, get_current_user
from app.config import get_settings
from app.contracts.outcomes import AppError, Outcome
from app.datasets.ingest import ingest_csv
from app.datasets.profile import jsonable, open_parquet
from app.datasets.quality import quality_report
from app.db import dataset_parquet_path, get_conn

router = APIRouter(prefix="/api/datasets")
CurrentUser = Annotated[User, Depends(get_current_user)]
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]

_META = ("id", "name", "filename", "version", "row_count", "created_at", "updated_at")
_PREVIEW_ROWS = 20


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _meta(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in _META}


def _read_upload(conn: sqlite3.Connection, file: UploadFile) -> tuple[str, bytes]:
    """Return (base filename, bytes), refusing anything over the admin-set size limit."""
    filename = Path(file.filename or "").name or "upload.csv"
    limit_mb = read_system_settings(conn).max_upload_mb
    data = file.file.read(limit_mb * 1024 * 1024 + 1)
    if len(data) > limit_mb * 1024 * 1024:
        raise AppError(Outcome.invalid_input,
                       f"'{filename}': the file is larger than the {limit_mb} MB upload limit.")
    return filename, data


def _unique_name(conn: sqlite3.Connection, owner_id: str, stem: str) -> str:
    name, k = stem, 1
    while conn.execute("SELECT 1 FROM datasets WHERE owner_id = ? AND name = ?",
                       (owner_id, name)).fetchone():
        k += 1
        name = f"{stem}_{k}"
    return name


def _remove(conn: sqlite3.Connection, dataset_id: str) -> None:
    conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
    shutil.rmtree(dataset_parquet_path(dataset_id, 1).parent, ignore_errors=True)


def _create(conn: sqlite3.Connection, user: User, file: UploadFile) -> sqlite3.Row:
    filename, data = _read_upload(conn, file)
    dataset_id = uuid.uuid4().hex
    try:
        profile = ingest_csv(data, filename, dataset_parquet_path(dataset_id, 1))
        name = _unique_name(conn, user.id, Path(filename).stem)
        now = _now()
        conn.execute(
            "INSERT INTO datasets (id, owner_id, name, filename, version, row_count, profile_json,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (dataset_id, user.id, name, filename, profile["row_count"], json.dumps(profile),
             now, now),
        )
    except BaseException:
        shutil.rmtree(dataset_parquet_path(dataset_id, 1).parent, ignore_errors=True)
        raise
    return conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()


def _parquet(row: sqlite3.Row) -> Path:
    path = dataset_parquet_path(row["id"], row["version"])
    if not path.exists():
        raise AppError(Outcome.execution_failed,
                       "The stored data for this dataset is missing. Please upload it again.")
    return path


def _reference_date() -> date:
    configured = get_settings().demo_reference_date
    return date.fromisoformat(configured) if configured else date.today()


def _dependents(conn: sqlite3.Connection, user: User, dataset_id: str) -> dict:
    rows = conn.execute("SELECT id, dataset_versions_json FROM results WHERE owner_id = ?",
                        (user.id,)).fetchall()
    result_ids = [r["id"] for r in rows if dataset_id in json.loads(r["dataset_versions_json"])]
    pins = 0
    if result_ids:
        marks = ",".join("?" * len(result_ids))
        pins = conn.execute(f"SELECT count(*) FROM pins WHERE result_id IN ({marks})",  # noqa: S608
                            result_ids).fetchone()[0]
    return {"dataset_id": dataset_id, "results": len(result_ids), "pins": pins}


@router.post("", status_code=201)
@router.post("/", status_code=201, include_in_schema=False)
def upload_datasets(files: list[UploadFile], user: CurrentUser,
                    conn: Conn) -> list[dict]:
    """One or more CSV files. All succeed or none are kept."""
    created: list[sqlite3.Row] = []
    try:
        for file in files:
            created.append(_create(conn, user, file))
    except BaseException:
        for row in created:
            _remove(conn, row["id"])
        raise
    return [_meta(r) for r in created]


@router.get("")
@router.get("/", include_in_schema=False)
def list_datasets(user: CurrentUser,
                  conn: Conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM datasets WHERE owner_id = ? ORDER BY created_at, name",
                        (user.id,)).fetchall()
    return [_meta(r) for r in rows]


@router.get("/{dataset_id}")
def get_dataset(dataset_id: str, user: CurrentUser,
                conn: Conn) -> dict:
    row = authz.require_owned(conn, "datasets", dataset_id, user, Action.read)
    cur = open_parquet(_parquet(row)).execute("SELECT * FROM data LIMIT ?", [_PREVIEW_ROWS])
    preview = {"columns": [d[0] for d in cur.description],
               "rows": [[jsonable(v) for v in r] for r in cur.fetchall()]}
    return {**_meta(row), "profile": json.loads(row["profile_json"]), "preview": preview}


@router.get("/{dataset_id}/quality")
def get_quality(dataset_id: str, user: CurrentUser,
                conn: Conn) -> dict:
    row = authz.require_owned(conn, "datasets", dataset_id, user, Action.read)
    return quality_report(_parquet(row), json.loads(row["profile_json"]), _reference_date())


@router.get("/{dataset_id}/dependents")
def get_dependents(dataset_id: str, user: CurrentUser,
                   conn: Conn) -> dict:
    authz.require_owned(conn, "datasets", dataset_id, user, Action.read)
    return _dependents(conn, user, dataset_id)


@router.put("/{dataset_id}")
def replace_dataset(dataset_id: str, file: UploadFile, user: CurrentUser,
                    conn: Conn) -> dict:
    """Replace the file: version + 1. Old Parquet versions are kept."""
    row = authz.require_owned(conn, "datasets", dataset_id, user, Action.modify)
    filename, data = _read_upload(conn, file)
    version = row["version"] + 1
    profile = ingest_csv(data, filename, dataset_parquet_path(dataset_id, version))
    conn.execute(
        "UPDATE datasets SET filename = ?, version = ?, row_count = ?, profile_json = ?,"
        " updated_at = ? WHERE id = ?",
        (filename, version, profile["row_count"], json.dumps(profile), _now(), dataset_id),
    )
    return _meta(conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone())


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str, user: CurrentUser, conn: Conn,
                   confirm: bool = False) -> dict:
    row = authz.require_owned(conn, "datasets", dataset_id, user, Action.delete)
    if not confirm:
        dep = _dependents(conn, user, dataset_id)
        raise AppError(
            Outcome.invalid_input,
            f"Deleting '{row['name']}' affects {dep['results']} saved result(s) and "
            f"{dep['pins']} pinned item(s), which will show the data as missing. "
            "Repeat the request with confirm=true to delete it.")
    _remove(conn, dataset_id)
    return {"deleted": dataset_id}
