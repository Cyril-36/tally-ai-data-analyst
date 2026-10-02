"""One bounded OpenAI-compatible tool-calling adapter; never used by offline tests."""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from app.config import get_settings
from app.contracts.outcomes import AppError, Outcome

log = logging.getLogger("app.provider")
_PING_TIMEOUT_S = 8.0
_PING_MAX_TOKENS = 8


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_STRING = {"type": "string"}
TOOLS = [
    _tool("list_datasets", "List selected, authorized datasets", {}, []),
    _tool(
        "inspect_dataset",
        "Inspect column types and small data samples",
        {"dataset": _STRING},
        ["dataset"],
    ),
    _tool(
        "check_data_quality",
        "Check selected data without modifying it",
        {"dataset": _STRING},
        ["dataset"],
    ),
    _tool(
        "check_join",
        "Check a relationship before analysis; join_json is one JSON Join object",
        {"join_json": _STRING},
        ["join_json"],
    ),
    _tool(
        "run_analysis",
        "Execute one validated AnalysisSpec JSON object, never SQL; returns result_id",
        {"spec_json": _STRING},
        ["spec_json"],
    ),
    _tool(
        "detect_anomalies",
        "Check an uncapped result using robust z scores",
        {"result_id": _STRING, "metric": _STRING},
        ["result_id", "metric"],
    ),
    _tool(
        "make_chart",
        "Change encoding over an existing result; chart_json is one ChartSpec JSON object",
        {"result_id": _STRING, "chart_json": _STRING},
        ["result_id", "chart_json"],
    ),
    _tool(
        "finish",
        "Select a computed result for the final answer",
        {"result_id": _STRING},
        ["result_id"],
    ),
    _tool(
        "clarify",
        "Ask before guessing an ambiguous metric, date or dataset",
        {
            "reason": {"type": "string", "enum": ["metric", "date", "dataset"]},
            "options": {"type": "array", "items": _STRING, "maxItems": 6},
        },
        ["reason"],
    ),
    _tool(
        "unsupported",
        "Stop when available data or supported operations cannot answer",
        {"reason": {"type": "string", "enum": ["missing_data", "operation"]}},
        ["reason"],
    ),
]


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Reply:
    calls: list[ToolCall]
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    assistant_message: dict = field(default_factory=dict)


class Provider:
    def ping(self) -> str:
        """One minimal request to check that the provider answers; returns the model name.
        Raises AppError(provider_failed) without any provider text."""
        settings = get_settings()
        if not settings.llm_api_key:
            raise AppError(Outcome.provider_failed, "The AI provider is not configured.")
        try:
            with OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=_PING_TIMEOUT_S,
                max_retries=0,
            ) as client:
                response = client.chat.completions.create(
                    model=settings.llm_model,
                    messages=[{"role": "user", "content": "ping"}],
                    temperature=0,
                    max_tokens=_PING_MAX_TOKENS,
                )
            if not response.choices:
                raise ValueError("empty response")
        except Exception as exc:
            log.warning("provider ping failed: type=%s status=%s", type(exc).__name__,
                        getattr(exc, "status_code", None))
            raise AppError(Outcome.provider_failed,
                           "The AI provider did not respond correctly.") from None
        return settings.llm_model

    def complete(self, messages: list[dict], remaining_s: float, max_tokens: int) -> Reply:
        settings = get_settings()
        if not settings.llm_api_key:
            raise AppError(Outcome.provider_failed, "The AI provider is not configured.")
        try:
            # No hidden SDK retries: the orchestrator owns the budget.
            with OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=min(settings.llm_timeout_s, remaining_s),
                max_retries=0,
            ) as client:
                response = client.chat.completions.create(
                    model=settings.llm_model,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="required",
                    temperature=0,
                    max_tokens=max_tokens,
                )
            message = response.choices[0].message
            calls = [
                ToolCall(c.id, c.function.name, json.loads(c.function.arguments))
                for c in message.tool_calls or []
            ]
            if not calls or any(not isinstance(c.args, dict) for c in calls):
                raise ValueError("Missing valid tool calls")
            usage = response.usage
            return Reply(
                calls,
                usage.total_tokens if usage else max_tokens,
                usage.prompt_tokens if usage else 0,
                usage.completion_tokens if usage else 0,
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.args)},
                        }
                        for c in calls
                    ],
                },
            )
        except AppError:
            raise
        except Exception as exc:
            # Provider errors may contain credentials or endpoint details; never echo them.
            reason = (
                "no_tool_calls" if str(exc) == "Missing valid tool calls"
                else "invalid_tool_json" if isinstance(exc, json.JSONDecodeError)
                else "other"
            )
            log.warning(
                "provider request failed: type=%s status=%s reason=%s",
                type(exc).__name__,
                getattr(exc, "status_code", None),
                reason,
            )
            raise AppError(
                Outcome.provider_failed,
                "The AI provider failed or returned an invalid response. Try again.",
            ) from None


def fake_provider_enabled() -> bool:
    return get_settings().llm_provider.strip().lower() == "fake"


def get_provider() -> Provider:
    if fake_provider_enabled():
        from app.agent.fake_provider import ScriptedProvider

        return ScriptedProvider()
    return Provider()
