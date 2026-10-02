"""Score live agent behavior on schemas outside the retail development fixture.

Suites: ``transfer`` (support and subscriptions) and ``holdout`` (logistics, asked in ordinary
business language). Each suite only uploads its own CSVs, so suites stay comparable."""

# This runnable script adds the backend checkout before importing its app package.
# ruff: noqa: E402

import argparse
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import holdout_cases
import transfer_cases
from run_live import (
    HEADERS,
    _create_conversation,
    _report_header,
    _score,
    _sha,
    _write_report,
)

from app.config import get_settings
from app.main import create_app

SUITES = {
    "transfer": (ROOT / "evals" / "transfer", transfer_cases),
    "holdout": (ROOT / "evals" / "holdout", holdout_cases),
}


def run(check_only: bool, suite: str = "transfer") -> int:
    fixtures, module = SUITES[suite]
    cases = module.CASES
    logging.getLogger("app").setLevel(logging.WARNING)
    settings = get_settings()
    if not check_only and settings.llm_provider != "fake" and not settings.llm_api_key:
        print(f"Set LLM_API_KEY for the live {suite} evaluation.", file=sys.stderr)
        return 2
    header = _report_header(settings)
    header.update({
        "suite": suite,
        "fixture_sha256": {p.name: _sha(p) for p in sorted(fixtures.glob("*.csv"))},
        "case_set_sha256": _sha(Path(module.__file__)),
        "reference_date": "not used",
    })
    rows = []
    with tempfile.TemporaryDirectory(prefix="analyst-transfer-") as data_dir:
        os.environ["DATA_DIR"] = data_dir
        get_settings.cache_clear()
        with TestClient(create_app(), headers=HEADERS) as client:
            signup = client.post("/api/auth/signup", json={
                "email": "transfer@example.com", "password": "temporary-eval-password"
            })
            if signup.status_code != 201:
                raise RuntimeError(f"Transfer sign-up failed: {signup.status_code}")
            files = [
                ("files", (p.name, p.read_bytes(), "text/csv"))
                for p in sorted(fixtures.glob("*.csv"))
            ]
            uploaded = client.post("/api/datasets", files=files)
            if uploaded.status_code != 201:
                raise RuntimeError(f"Transfer upload failed: {uploaded.status_code}")
            ids = [item["id"] for item in uploaded.json()]
            baseline_id = _create_conversation(client, ids, "Transfer baselines")
            consecutive_provider_failures = 0
            groups: dict[str, str] = {}
            for case in cases:
                expected = None
                if case.spec is not None:
                    baseline = client.post(
                        f"/api/conversations/{baseline_id}/analyze",
                        json={"question": case.question, "spec": case.spec,
                              "request_key": f"{suite}-golden-{case.id}"},
                    )
                    if baseline.status_code != 200:
                        raise RuntimeError(f"Invalid transfer spec {case.id}: {baseline.text}")
                    expected = baseline.json()["table"]
                if check_only:
                    rows.append({"id": case.id, "status": "baseline_valid"})
                    continue
                if consecutive_provider_failures >= 3:
                    rows.append({"id": case.id, "status": "not_run_provider_unavailable"})
                    continue
                # Cases in one context group share a conversation, so follow-ups see earlier turns.
                group = case.context_group or case.id
                if group not in groups:
                    groups[group] = _create_conversation(client, ids, case.id)
                conversation = groups[group]
                started = time.monotonic()
                response = client.post(
                    f"/api/conversations/{conversation}/ask",
                    json={"question": case.question, "request_key": f"{suite}-live-{case.id}"},
                )
                actual = response.json()
                score = _score(case, actual, expected, {})
                usage = next(
                    (
                        step["provider_usage"]
                        for step in reversed(actual.get("trace", []))
                        if "provider_usage" in step
                    ),
                    {},
                )
                failed_provider = (
                    response.status_code == 502 or score["outcome"] == "provider_failed"
                )
                consecutive_provider_failures = (
                    consecutive_provider_failures + 1 if failed_provider else 0
                )
                entry = {
                    "id": case.id,
                    "status": "completed" if response.status_code == 200 else "http_error",
                    "http_status": response.status_code,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "tokens": usage.get("tokens"),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "score": score,
                }
                # Saved for every case, not only failures, so accepted answers can be audited.
                entry["diagnostics"] = {
                    "actual_result": actual,
                    "expected_spec": case.spec,
                    "expected_table": expected,
                }
                rows.append(entry)
    if check_only:
        print(f"Validated {len(rows)} {suite} golden specs/outcomes without AI calls.")
        return 0
    report = {
        **header, "cases": rows,
        "correct": sum(bool(row.get("score", {}).get("correct")) for row in rows),
        "strict_correct": sum(bool(row.get("score", {}).get("strict_correct")) for row in rows),
        "not_run": sum(row["status"].startswith("not_run") for row in rows),
    }
    path = _write_report(report)
    score = f"{report['correct']}/{len(rows)}"
    print(f"{suite.capitalize()} evaluation: {score} correct; report: {path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--suite", choices=sorted(SUITES), default="transfer")
    args = parser.parse_args()
    raise SystemExit(run(args.check, args.suite))
