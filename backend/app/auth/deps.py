"""Interface used by every router. Implemented by the auth track."""

from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    id: str
    email: str
    role: str  # "analyst" | "admin"


def get_current_user() -> User:  # replaced by the auth track; overridable in tests
    raise NotImplementedError
