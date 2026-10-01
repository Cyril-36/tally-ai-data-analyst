"""Authorization rules (PLAN.md section 7). Every route AND every agent tool calls
these; nothing checks ownership ad hoc.

Rule 1 (ownership): datasets, conversations, results, pins belong to their creator.
  Another user's resource is reported as not found (existence is not revealed).
Rule 2 (admin): only admins change system settings. Admin grants NO data access.
Sharing is not in v1, so every non-owner access is refused.
"""

import logging
import sqlite3
from enum import StrEnum

from app.auth.deps import User
from app.contracts.outcomes import AppError, Outcome

_OWNED_TABLES = {"datasets": "Dataset", "conversations": "Conversation",
                 "results": "Result", "pins": "Pinned item"}
log = logging.getLogger("app.authz")


class Action(StrEnum):
    read = "read"
    analyze = "analyze"
    export = "export"
    modify = "modify"
    delete = "delete"


def require_owned(
    conn: sqlite3.Connection, table: str, resource_id: str, user: User, action: Action
) -> sqlite3.Row:
    """Return the row if ``user`` may perform ``action`` on it, else raise not_found.
    ``action`` is recorded for logging; in v1 every action requires ownership."""
    label = _OWNED_TABLES[table]  # KeyError = programming error, not user input
    row = conn.execute(
        f"SELECT * FROM {table} WHERE id = ? AND owner_id = ?",  # noqa: S608 - table is allow-listed
        (resource_id, user.id),
    ).fetchone()
    if row is None:
        log.info("access_refused", extra={"user_id": user.id, "resource": table,
                                          "action": action.value})
        raise AppError(Outcome.not_found, f"{label} not found or you don't have access to it.")
    return row


def require_admin(user: User) -> None:
    if user.role != "admin":
        raise AppError(Outcome.permission_denied, "Only an admin can change system settings.")
