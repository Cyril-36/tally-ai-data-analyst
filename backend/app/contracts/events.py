"""Server-sent events for /api/conversations/{id}/ask.

Progress events stream immediately. ``answer`` is emitted only after the claims have
been validated; unchecked narrative never reaches the client. Exactly one terminal
event (``result`` or ``error``) ends the stream. See docs/PLAN.md section 5.
"""

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class EventType(StrEnum):
    planning = "planning"  # {"step": str}
    tool_call = "tool_call"  # {"tool": str, "args": dict}
    tool_result = "tool_result"  # {"tool": str, "summary": str, "ok": bool}
    executing = "executing"  # {"sql": str}
    validating = "validating"  # {"claims": int}
    result = "result"  # AnalysisResult JSON (terminal)
    error = "error"  # ErrorBody JSON (terminal)


class Event(BaseModel):
    type: EventType
    data: dict[str, Any]

    def to_sse(self) -> str:
        return f"event: {self.type.value}\ndata: {json.dumps(self.data, default=str)}\n\n"
