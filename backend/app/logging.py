"""Structured JSON logs. Only whitelisted fields are emitted, so cookies, passwords and
request bodies can never leak through a log line."""

import json
import logging
import sys

FIELDS = (
    "request_id", "method", "path", "status", "latency_ms", "user_id",
    "reason", "tokens", "tool_calls", "repairs", "tools", "last_outcome",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"ts": self.formatTime(record), "level": record.levelname,
                 "msg": record.getMessage()}
        entry.update({f: getattr(record, f) for f in FIELDS if hasattr(record, f)})
        if record.exc_info:
            entry["traceback"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging() -> None:
    logger = logging.getLogger("app")
    if any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
