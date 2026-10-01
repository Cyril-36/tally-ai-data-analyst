"""Run every frozen question through the real agent and score against deterministic specs.

Use ``--check`` to validate the expected analyses offline without an AI key. A live run
needs LLM_API_KEY, writes every case (including failures), and never prints the key.
"""

# This runnable script adds the backend checkout before importing its app package.
# ruff: noqa: E402

import argparse
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from cases import CASES

from app.agent.composer import compose
from app.agent.provider import TOOLS
from app.agent.service import SYSTEM
from app.agent.validator import validate_claims
from app.charts.builder import default_chart, validate_chart
from app.config import get_settings
from app.contracts.result import AnalysisResult
from app.contracts.spec import AnalysisSpec
from app.main import create_app

SAMPLE = ROOT / "data" / "sample"
REPORTS = ROOT / "evals" / "reports"
HEADERS = {"X-Requested-With": "eval"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is required for evaluation provenance.")
    return subprocess.run(  # noqa: S603 - executable and callers are fixed in this module
        [git, *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _create_conversation(client: TestClient, ids: list[str], title: str) -> str:
    response = client.post("/api/conversations", json={"title": title, "dataset_ids": ids})
    if response.status_code != 201:
        raise RuntimeError(f"Cannot create evaluation conversation: {response.status_code}")
    return response.json()["id"]


def _normalized_rows(rows: list[list]) -> list[str]:
    def cell(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value):
                return None
            return round(value, 8)
        return value

    return sorted(json.dumps([cell(v) for v in row], sort_keys=True) for row in rows)


def _same_table(actual: dict | None, expected: dict) -> bool:
    if actual is None:
        return False
    actual_columns = actual.get("columns", [])
    expected_columns = expected["columns"]
    if len(actual_columns) != len(expected_columns):
        return False
    for observed, target in zip(actual_columns, expected_columns, strict=True):
        if observed.get("role") != target["role"]:
            return False
        if target["role"] != "metric" and observed.get("name") != target["name"]:
            return False
    return _normalized_rows(actual["rows"]) == _normalized_rows(expected["rows"])


def _same_spec(actual: dict | None, expected: dict) -> bool:
    if actual is None:
        return False
    observed = AnalysisSpec.model_validate(actual).model_dump(mode="json")
    target = AnalysisSpec.model_validate(expected).model_dump(mode="json")
    if len(observed["metrics"]) != len(target["metrics"]):
        return False
    aliases = {}
    for current, goal in zip(observed["metrics"], target["metrics"], strict=True):
        aliases[current["name"]] = goal["name"]
        current["name"] = goal["name"]
    for sort in observed["sort"]:
        sort["by"] = aliases.get(sort["by"], sort["by"])
    return observed == target


def _truth() -> dict:
    text = (SAMPLE / "ANOMALIES.md").read_text()
    return json.loads(re.search(r"```json\n(.*?)\n```", text, re.DOTALL).group(1))


def _score(case, actual: dict, expected: dict | None, truth: dict) -> dict:
    outcome = actual.get("outcome")
    status_ok = outcome == case.outcome
    score = {
        "outcome": outcome,
        "outcome_correct": status_ok,
        "spec_correct": None,
        "table_correct": None,
        "claims_valid": None,
        "chart_valid": None,
        "anomaly_precision": None,
        "anomaly_recall": None,
    }
    if expected is not None:
        score["table_correct"] = _same_table(actual.get("table"), expected)
        try:
            result = AnalysisResult.model_validate(actual)
            score["spec_correct"] = _same_spec(actual.get("spec"), case.spec) if case.spec else None
            canonical = compose(result)
            score["claims_valid"] = (
                bool(result.claims)
                and len(validate_claims(result)) == len(result.claims)
                and result.claims == canonical.claims
                and result.answer_sentences == canonical.answer_sentences
            )
            if result.chart and result.table:
                validate_chart(result.table, result.chart)
                score["chart_valid"] = True
            else:
                score["chart_valid"] = (
                    result.table is not None and default_chart(result.table) is None
                )
        except (ValueError, TypeError):
            score["spec_correct"] = False if case.spec else None
            score["claims_valid"] = False
            score["chart_valid"] = False
    if case.anomaly_metric:
        report = actual.get("anomalies") or {}
        flags = report.get("flags", []) if isinstance(report, dict) else []
        expected_flags = {
            (truth["west_spike"]["region"], truth["west_spike"]["month"]),
            (truth["south_drop"]["region"], truth["south_drop"]["month"]),
        }
        actual_flags = set()
        valid_evidence = report.get("status") == "completed_found"
        for flag in flags:
            if not isinstance(flag, dict) or not isinstance(flag.get("row_key"), dict):
                valid_evidence = False
                continue
            key = flag["row_key"]
            if set(key) != {"region", "period"}:
                valid_evidence = False
            actual_flags.add((key.get("region"), str(key.get("period"))[:7]))
            if flag.get("metric") != case.anomaly_metric or expected is None:
                valid_evidence = False
                continue
            names = [column["name"] for column in expected["columns"]]
            matches = [
                row[names.index(case.anomaly_metric)]
                for row in expected["rows"]
                if all(
                    str(row[names.index(name)]) == str(value)
                    for name, value in key.items()
                    if name in names
                )
            ]
            if (
                len(matches) != 1
                or isinstance(flag.get("value"), bool)
                or not isinstance(flag.get("value"), (int, float))
            ) or not math.isclose(float(flag["value"]), float(matches[0]), rel_tol=1e-8):
                valid_evidence = False
        if len(actual_flags) != len(flags):
            valid_evidence = False
        hits = len(actual_flags & expected_flags)
        score["anomaly_precision"] = hits / len(actual_flags) if actual_flags else 0.0
        score["anomaly_recall"] = hits / len(expected_flags)
        score["anomaly_evidence_valid"] = valid_evidence
    score["correct"] = status_ok and (score["table_correct"] is not False)
    if expected is not None:
        score["correct"] = (
            score["correct"]
            and score["spec_correct"] is not False
            and score["claims_valid"] is True
            and score["chart_valid"] is True
        )
    if case.anomaly_metric:
        score["correct"] = (
            score["correct"]
            and score["anomaly_precision"] == 1.0
            and score["anomaly_recall"] == 1.0
            and score["anomaly_evidence_valid"] is True
        )
    return score


def _report_header(settings) -> dict:
    paths = [SAMPLE / f"{name}.csv" for name in ("orders", "customers", "products")]
    truth = _truth()
    return {
        "run_at": datetime.now(UTC).isoformat(),
        "model": settings.llm_model,
        "temperature": 0,
        "prompt_config_sha256": hashlib.sha256(
            (SYSTEM + json.dumps(TOOLS, sort_keys=True)).encode()
        ).hexdigest(),
        "fixture_sha256": {p.name: _sha(p) for p in paths},
        "reference_date": truth["reference_date"],
        "git_sha": _git("rev-parse", "HEAD"),
        "dirty_tree": bool(_git("status", "--porcelain")),
        "cost": None,
        "cost_note": "Provider billing rates were not available to this runner.",
    }


def _write_report(report: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = REPORTS / f"{name}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Live model evaluation",
        "",
        f"Model: `{report['model']}`. Reference date: `{report['reference_date']}`. "
        f"Git: `{report['git_sha']}` (dirty: `{report['dirty_tree']}`).",
        "",
        f"Correct: {report['correct']}/{len(report['cases'])}; "
        f"not run: {report['not_run']}. Cost: unknown.",
        "",
        "| Case | Outcome | Table | Claims | Chart | Latency ms | Tokens |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for case in report["cases"]:
        score = case.get("score", {})
        lines.append(
            f"| {case['id']} | {score.get('outcome', case['status'])} | "
            f"{score.get('table_correct')} | {score.get('claims_valid')} | "
            f"{score.get('chart_valid')} | {case.get('latency_ms', '')} | "
            f"{case.get('tokens', '')} |"
        )
    path.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return path


def run(check_only: bool) -> int:
    logging.getLogger("app").setLevel(logging.WARNING)
    settings = get_settings()
    if not check_only and not settings.llm_api_key:
        print(
            "Set LLM_API_KEY before a live evaluation; --check validates baselines offline.",
            file=sys.stderr,
        )
        return 2
    header = _report_header(settings)
    truth = _truth()
    with tempfile.TemporaryDirectory(prefix="analyst-eval-") as data_dir:
        os.environ["DATA_DIR"] = data_dir
        get_settings.cache_clear()
        with TestClient(create_app(), headers=HEADERS) as client:
            registered = client.post(
                "/api/auth/signup",
                json={"email": "eval@example.com", "password": "temporary-eval-password"},
            )
            if registered.status_code != 201:
                raise RuntimeError(f"Evaluation sign-up failed: {registered.status_code}")
            files = [
                ("files", (f"{name}.csv", (SAMPLE / f"{name}.csv").read_bytes(), "text/csv"))
                for name in ("orders", "customers", "products")
            ]
            uploaded = client.post("/api/datasets", files=files)
            if uploaded.status_code != 201:
                raise RuntimeError(f"Fixture upload failed: {uploaded.status_code}")
            ids = [item["id"] for item in uploaded.json()]
            baseline_id = _create_conversation(client, ids, "Golden baselines")
            groups = {}
            rows = []
            consecutive_provider_failures = 0
            for case in CASES:
                expected = None
                if case.spec is not None:
                    baseline = client.post(
                        f"/api/conversations/{baseline_id}/analyze",
                        json={
                            "question": case.question,
                            "spec": case.spec,
                            "request_key": f"golden-{case.id}",
                        },
                    )
                    if baseline.status_code != 200:
                        raise RuntimeError(f"Invalid golden spec for {case.id}: {baseline.text}")
                    expected = baseline.json()["table"]
                if check_only:
                    rows.append({"id": case.id, "status": "baseline_valid"})
                    continue
                if consecutive_provider_failures >= 3:
                    rows.append({"id": case.id, "status": "not_run_provider_unavailable"})
                    continue
                group = case.context_group or case.id
                if group not in groups:
                    groups[group] = _create_conversation(client, ids, case.id)
                started = time.monotonic()
                response = client.post(
                    f"/api/conversations/{groups[group]}/ask",
                    json={"question": case.question, "request_key": f"live-{case.id}"},
                )
                actual = response.json()
                score = _score(case, actual, expected, truth)
                failed_provider = (
                    response.status_code == 502 or score["outcome"] == "provider_failed"
                )
                consecutive_provider_failures = (
                    consecutive_provider_failures + 1 if failed_provider else 0
                )
                usage = next(
                    (
                        step["provider_usage"]
                        for step in reversed(actual.get("trace", []))
                        if "provider_usage" in step
                    ),
                    {},
                )
                rows.append(
                    {
                        "id": case.id,
                        "status": "completed" if response.status_code == 200 else "http_error",
                        "http_status": response.status_code,
                        "message": actual.get("message") if response.status_code != 200 else None,
                        "latency_ms": round((time.monotonic() - started) * 1000),
                        "tokens": usage.get("tokens"),
                        "score": score,
                    }
                )
    if check_only:
        print(f"Validated {len(rows)} frozen golden specs/outcomes without AI calls.")
        return 0
    report = {
        **header,
        "cases": rows,
        "correct": sum(bool(row.get("score", {}).get("correct")) for row in rows),
        "not_run": sum(row["status"].startswith("not_run") for row in rows),
    }
    path = _write_report(report)
    print(f"Live evaluation: {report['correct']}/{len(rows)} correct; report: {path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="validate golden specs without AI calls"
    )
    args = parser.parse_args()
    raise SystemExit(run(args.check))
