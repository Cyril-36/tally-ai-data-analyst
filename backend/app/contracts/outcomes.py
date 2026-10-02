"""Explicit outcomes. None of the non-ok outcomes may be rendered as a successful
answer. See docs/PLAN.md principle 4."""

from enum import StrEnum

from pydantic import BaseModel


class Outcome(StrEnum):
    ok = "ok"
    permission_denied = "permission_denied"
    not_found = "not_found"
    needs_clarification = "needs_clarification"
    unsupported = "unsupported"
    insufficient_data = "insufficient_data"
    execution_failed = "execution_failed"
    provider_failed = "provider_failed"
    budget_exhausted = "budget_exhausted"
    narrative_unverified = "narrative_unverified"
    invalid_input = "invalid_input"


HTTP_STATUS = {
    Outcome.permission_denied: 403,
    Outcome.not_found: 404,
    Outcome.invalid_input: 422,
    Outcome.unsupported: 422,
    Outcome.execution_failed: 500,
    Outcome.provider_failed: 502,
    Outcome.budget_exhausted: 422,
}


class AppError(Exception):
    """Raise anywhere; the API layer maps it to ``ErrorBody`` with HTTP_STATUS.
    Messages are user-facing: never include another user's identity, file paths,
    secrets, or stack traces."""

    def __init__(self, outcome: Outcome, message: str):
        super().__init__(message)
        self.outcome = outcome
        self.message = message


class ErrorBody(BaseModel):
    outcome: Outcome
    message: str
