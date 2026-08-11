#!/usr/bin/env python3
"""Fail-closed V3.3 Knowledge Synthesis production acceptance gate."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "FINAL_PRODUCTION_AUDIT"
EXPECTED_MATRIX = {
    ("windows-latest", "3.11"), ("windows-latest", "3.12"),
    ("ubuntu-latest", "3.11"), ("ubuntu-latest", "3.12"),
    ("macos-latest", "3.11"), ("macos-latest", "3.12"),
}
FIXTURE_HASHES = {
    "tests/fixtures/real_failures/case_a/RUN.original.json": "c805af6ba8724be73ce1855e14c9771a83716cbbff95f7d4655a06a0cd497191",
    "tests/fixtures/real_failures/case_a/binding-cache.original.json": "5c6ffb2b26e19400eb2f5849639ea2fa86e7ef985eb888f7b6c811765a4ced40",
    "tests/fixtures/real_failures/case_b/RUN.original.json": "0d08f4b659ae5b35dc813963fd5c1b27b5a5d087d7873067f3d9c755fcd7ba3d",
}
ENCODED_FIXTURE_HASHES = {
    "tests/fixtures/real_failures/case_b/RUN.ev629.original.json.b64": "598b2d958aa43e382df54e5cae861b3060de6e9de1c32342c3ca790bb9c1a771",
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def command_result(name: str, command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    log = AUDIT / f"{name}.log"
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    return {
        "name": name,
        "passed": completed.returncode == 0,
        "exit_code": completed.returncode,
        "command": command,
        "log": log.relative_to(ROOT).as_posix(),
        "log_sha256": sha256(log),
        "reason": "" if completed.returncode == 0 else (completed.stderr or completed.stdout)[-2000:],
    }


def fixture_result() -> dict[str, Any]:
    failures: list[str] = []
    observed: dict[str, str] = {}
    for relative, expected in FIXTURE_HASHES.items():
        path = ROOT / relative
        actual = sha256(path) if path.is_file() else "missing"
        observed[relative] = actual
        if actual != expected:
            failures.append(f"{relative}: expected {expected}, got {actual}")
    for relative, expected in ENCODED_FIXTURE_HASHES.items():
        path = ROOT / relative
        try:
            decoded = base64.b64decode("".join(path.read_text(encoding="ascii").split()), validate=True)
            actual = hashlib.sha256(decoded).hexdigest()
        except (OSError, UnicodeError, ValueError) as exc:
            actual = f"invalid: {exc}"
        observed[relative] = actual
        if actual != expected:
            failures.append(f"{relative}: expected decoded hash {expected}, got {actual}")
    return {
        "name": "immutable_real_failure_fixtures",
        "passed": not failures,
        "observed": observed,
        "reason": "; ".join(failures),
    }


def matrix_result(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"name": "github_six_environment_ci", "passed": False, "reason": "No consolidated GitHub Actions evidence was supplied."}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        jobs = payload["jobs"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return {"name": "github_six_environment_ci", "passed": False, "reason": f"Invalid CI evidence: {exc}"}
    found = {(str(item.get("os")), str(item.get("python"))) for item in jobs}
    failed = [item for item in jobs if item.get("status") != "passed" or int(item.get("pytest_exit_code", 1)) != 0]
    commit_shas = {str(item.get("commit_sha", "")) for item in jobs}
    valid_commit = len(commit_shas) == 1 and next(iter(commit_shas), "") not in {"", "unknown"}
    passed = found == EXPECTED_MATRIX and len(jobs) == 6 and not failed and valid_commit
    reasons: list[str] = []
    if found != EXPECTED_MATRIX or len(jobs) != 6:
        reasons.append(f"matrix mismatch: found {sorted(found)}")
    if failed:
        failed_names = [str(item.get("os")) + "/" + str(item.get("python")) for item in failed]
        reasons.append(f"failed jobs: {failed_names}")
    if not valid_commit:
        reasons.append("jobs do not attest one non-empty commit SHA")
    return {
        "name": "github_six_environment_ci", "passed": passed,
        "reason": "; ".join(reasons), "commit_sha": next(iter(commit_shas), ""), "jobs": jobs,
        "run_url": payload.get("run_url", ""),
    }


def regression_result(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {
            "name": "stock_operator_real_regression",
            "passed": False,
            "reason": "Verified V3.3 stock-operator regression evidence was not supplied.",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"name": "stock_operator_real_regression", "passed": False, "reason": f"Invalid regression evidence: {exc}"}
    checks = payload.get("release_checks", {})
    methods = int(payload.get("method_cluster_count", 0))
    skills = int(payload.get("skill_candidate_count", 0))
    passed = (
        payload.get("engine_package_version") == "3.3.0"
        and payload.get("status") == "passed"
        and 3 <= methods <= 8
        and 3 <= skills <= 8
        and all(bool(value) for value in checks.values())
    )
    reasons: list[str] = []
    if payload.get("engine_package_version") != "3.3.0":
        reasons.append("engine version is not 3.3.0")
    if payload.get("status") != "passed":
        reasons.append("regression status is not passed")
    if not 3 <= methods <= 8:
        reasons.append(f"Method Cluster count is {methods}, expected 3-8")
    if not 3 <= skills <= 8:
        reasons.append(f"Skill Candidate count is {skills}, expected 3-8")
    failed_checks = [name for name, value in checks.items() if not value]
    if failed_checks:
        reasons.append(f"failed checks: {failed_checks}")
    return {
        "name": "stock_operator_real_regression",
        "passed": passed,
        "reason": "; ".join(reasons),
        "run_id": payload.get("run_id"),
        "source_id": payload.get("source_id"),
        "conversion_id": payload.get("conversion_id"),
        "method_cluster_count": methods,
        "skill_candidate_count": skills,
        "evidence_path": str(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-only", action="store_true", help="Run local checks without claiming final READY.")
    parser.add_argument("--skip-tests", action="store_true", help="For CI aggregation after matrix jobs already ran the suite.")
    parser.add_argument("--ci-evidence", type=Path)
    parser.add_argument("--regression-evidence", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    AUDIT.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    checks.append({
        "name": "version", "passed": (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "3.3.0",
        "reason": "VERSION must be 3.3.0",
    })
    checks.append(fixture_result())
    checks.append(command_result("compile", [sys.executable, "-m", "compileall", "-q", "src", "tests"]))
    if not args.skip_tests:
        checks.append(command_result("pytest", [sys.executable, "-m", "pytest", "-q"]))
    else:
        checks.append({"name": "pytest", "passed": True, "reason": "skipped in aggregator; per-job evidence is evaluated below"})
    if not args.local_only:
        checks.append(matrix_result(args.ci_evidence))
        checks.append(regression_result(args.regression_evidence))
    blockers = [item for item in checks if not item["passed"]]
    payload = {
        "schema_version": 1,
        "engine_version": "3.3.0",
        "generated_at": now(),
        "os": os.environ.get("RUNNER_OS", platform.system()),
        "matrix_os": os.environ.get("MATRIX_OS", "local"),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "matrix_python": os.environ.get("MATRIX_PYTHON", f"{sys.version_info.major}.{sys.version_info.minor}"),
        "commit_sha": os.environ.get("GITHUB_SHA", "unknown"),
        "status": "passed" if not blockers else "blocked",
        "pytest_exit_code": next((item.get("exit_code", 0) for item in checks if item["name"] == "pytest"), 0),
        "checks": checks,
        "blockers": [{"item": item["name"], "reason": item.get("reason", "failed")} for item in blockers],
    }
    output = args.json_out or (AUDIT / "local_check.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if blockers:
        print("V3.3_BLOCKED")
        for blocker in payload["blockers"]:
            print(f"- {blocker['item']}: {blocker['reason']}")
        return 17
    if args.local_only:
        print("V3.3_LOCAL_CHECK_PASS")
    else:
        print("V3.3_KNOWLEDGE_SYNTHESIS_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
