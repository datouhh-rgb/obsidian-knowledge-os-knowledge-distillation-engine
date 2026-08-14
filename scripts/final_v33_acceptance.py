#!/usr/bin/env python3
"""Fail-closed V3.3.0 Final Production Verification gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOT = ROOT / "release/evidence/final_gate"
EXPECTED_MATRIX = {
    ("windows-latest", "3.11"), ("windows-latest", "3.12"),
    ("ubuntu-latest", "3.11"), ("ubuntu-latest", "3.12"),
    ("macos-latest", "3.11"), ("macos-latest", "3.12"),
}
PROTECTED_HASHES = {
    "src/knowledge_engine/identity.py": "92aca7792a093308189aca635e3cd36052acd99107478b48ca5d756bff55869e",
    "src/knowledge_engine/evidence_selector.py": "c9ece164c3cc7407b765cadcce0cb0820275eda595561a5c0af3873840a49c9c",
    "src/knowledge_engine/graph.py": "b6dd811fec3d706a6d2121d64e3f6a37fc3cc0614d00f60ec0c2ee31813f5556",
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"{path.relative_to(ROOT) if path.is_absolute() else path}: {exc}"
    return payload if isinstance(payload, dict) else None, "" if isinstance(payload, dict) else "JSON root is not an object"


def command_check(name: str, command: list[str]) -> dict[str, Any]:
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    log = AUDIT_ROOT / f"{name}.log"
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    return {
        "name": name, "passed": completed.returncode == 0,
        "exit_code": completed.returncode, "command": command,
        "log": log.relative_to(ROOT).as_posix(), "log_sha256": sha256(log),
        "reason": "" if completed.returncode == 0 else (completed.stderr or completed.stdout)[-2000:],
    }


def protected_contracts() -> dict[str, Any]:
    observed = {
        relative: sha256(ROOT / relative) if (ROOT / relative).is_file() else "missing"
        for relative in PROTECTED_HASHES
    }
    failures = [relative for relative, expected in PROTECTED_HASHES.items() if observed[relative] != expected]
    return {
        "name": "protected_v33_contracts_unchanged", "passed": not failures,
        "observed": observed, "reason": "hash mismatch: " + ", ".join(failures) if failures else "",
    }


def matrix_check(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"name": "github_six_environment_ci", "passed": False, "reason": "Current V3.3 matrix evidence is missing."}
    payload, error = load_json(path)
    if payload is None:
        return {"name": "github_six_environment_ci", "passed": False, "reason": error}
    jobs = payload.get("jobs", [])
    if not isinstance(jobs, list):
        jobs = []
    found = {(str(item.get("os")), str(item.get("python"))) for item in jobs if isinstance(item, dict)}
    commits = {str(item.get("commit_sha", "")) for item in jobs if isinstance(item, dict)}
    failed = [
        f"{item.get('os')}/{item.get('python')}"
        for item in jobs if not isinstance(item, dict) or item.get("job_status") != "passed"
    ]
    passed = (
        len(jobs) == 6 and found == EXPECTED_MATRIX and len(commits) == 1
        and "" not in commits and not failed and bool(payload.get("workflow_url"))
    )
    reasons: list[str] = []
    if len(jobs) != 6 or found != EXPECTED_MATRIX:
        reasons.append(f"matrix mismatch: {sorted(found)}")
    if len(commits) != 1 or "" in commits:
        reasons.append(f"commit SHA mismatch: {sorted(commits)}")
    if failed:
        reasons.append(f"failed jobs: {failed}")
    if not payload.get("workflow_url"):
        reasons.append("workflow URL missing")
    return {
        "name": "github_six_environment_ci", "passed": passed,
        "reason": "; ".join(reasons), "jobs": jobs,
        "commit_sha": next(iter(commits), ""), "workflow_url": payload.get("workflow_url", ""),
    }


def real_codex_check(path: Path) -> dict[str, Any]:
    payload, error = load_json(path)
    if payload is None:
        return {"name": "real_codex_discovery_and_call", "passed": False, "reason": error}
    checks = payload.get("checks", {}) if isinstance(payload.get("checks"), dict) else {}
    required = {
        "candidate_replayed_exactly", "candidate_before_human_review", "human_review_recorded",
        "approved_after_review", "no_auto_approve", "evidence_bound", "approval_real_codex_eval",
        "approved_only_deployment", "exposure_health", "implicit_invocation_off_during_exposure",
        "codex_discovery", "codex_explicit_call", "trigger_positive", "trigger_negative",
        "bad_description_block", "unexposed_after_test", "no_project_connection", "source_unchanged",
        "raw_evidence_integrity",
    }
    failed = sorted(name for name in required if checks.get(name) is not True)
    profile_ok = payload.get("profile") == "explicit_only" and payload.get("implicit_metrics_claimed") is False
    passed = payload.get("status") == "passed" and not failed and profile_ok
    return {
        "name": "real_codex_discovery_and_call", "passed": passed,
        "reason": (
            "failed checks: " + ", ".join(failed) if failed else
            ("explicit_only profile contract failed" if not profile_ok else ("status is not passed" if not passed else ""))
        ),
        "run_id": payload.get("run_id"), "codex_cli_version": payload.get("codex_cli_version"),
        "skill": payload.get("skill"), "review": payload.get("review"),
    }


def regression_check(name: str, path: Path) -> dict[str, Any]:
    payload, error = load_json(path)
    if payload is None:
        return {"name": name, "passed": False, "reason": error}
    checks = payload.get("checks", payload.get("release_checks", {}))
    failed = sorted(key for key, value in checks.items() if not value) if isinstance(checks, dict) else ["checks missing"]
    passed = payload.get("status") == "passed" and not failed
    return {
        "name": name, "passed": passed,
        "reason": "failed checks: " + ", ".join(failed) if failed else ("status is not passed" if not passed else ""),
        "run_id": payload.get("run_id"), "source_id": payload.get("source_id"),
        "conversion_id": payload.get("conversion_id"), "pipeline_status": payload.get("pipeline_status"),
    }


def release_identity_check(paths: list[Path | None]) -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout.strip()
    ref = os.environ.get("GITHUB_REF", "")
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    github_sha = os.environ.get("GITHUB_SHA", head)
    payloads = [load_json(path)[0] for path in paths if path is not None]
    evidence_commits = {
        str(payload.get("commit_sha", ""))
        for payload in payloads if isinstance(payload, dict)
    }
    trusted_ref = ref == "refs/heads/main" or ref == "refs/tags/v3.3.0"
    local_mode = not os.environ.get("GITHUB_ACTIONS")
    passed = (
        len(evidence_commits) == 1
        and head == github_sha
        and github_sha in evidence_commits
        and (trusted_ref or local_mode)
    )
    reasons: list[str] = []
    if len(evidence_commits) != 1 or github_sha not in evidence_commits:
        reasons.append(f"machine evidence commit mismatch: {sorted(evidence_commits)} != {github_sha}")
    if head != github_sha:
        reasons.append(f"checked-out HEAD {head} differs from GITHUB_SHA {github_sha}")
    if not trusted_ref and not local_mode:
        reasons.append(f"release ref {ref or '(missing)'} from event {event or '(missing)'} is not merged main or v3.3.0 tag")
    return {
        "name": "immutable_merged_or_tagged_release_identity",
        "passed": passed,
        "reason": "; ".join(reasons),
        "commit_sha": github_sha,
        "git_ref": ref,
        "event": event,
        "evidence_commits": sorted(evidence_commits),
    }


def release_layout_check() -> dict[str, Any]:
    required = [
        "release/FINAL_ACCEPTANCE_REPORT.md", "release/CI_REPORT.md",
        "release/REAL_CODEX_EXPLICIT_ONLY_REPORT.md", "release/BOOK_REGRESSION_REPORT.md",
        "release/CONVERSION_QUALITY_REPORT.md", "release/SKILL_SEMANTIC_QUALITY_REPORT.md",
        "history/V3.1_Audit", "history/V3.2_Audit",
        "history/V3.3_BLOCKED_Audit", "history/Failed_Regression_Cases",
        "FINAL_PRODUCTION_ACCEPTANCE_REPORT.md",
    ]
    missing = [item for item in required if not (ROOT / item).exists()]
    old_roots = [item for item in ("FINAL_AUDIT_PACK", "FINAL_LTS_AUDIT", "FINAL_PRODUCTION_AUDIT") if (ROOT / item).exists()]
    legacy_release_items = [
        item for item in ("release/CODEX_REAL_TEST_REPORT.md", "release/evidence/v33_prior")
        if (ROOT / item).exists()
    ]
    passed = not missing and not old_roots and not legacy_release_items
    return {
        "name": "release_history_isolation", "passed": passed,
        "reason": "; ".join([
            *( ["missing: " + ", ".join(missing)] if missing else [] ),
            *( ["old audit roots remain: " + ", ".join(old_roots)] if old_roots else [] ),
            *( ["legacy current-release items remain: " + ", ".join(legacy_release_items)] if legacy_release_items else [] ),
        ]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--ci-evidence", type=Path)
    parser.add_argument("--codex-evidence", type=Path, default=ROOT / "release/evidence/codex_real_test/codex_real_integration.json")
    parser.add_argument("--stock-regression", type=Path, default=ROOT / "release/evidence/regressions/stock_operator_v33_canonical.json")
    parser.add_argument("--cognitive-regression", type=Path, default=ROOT / "release/evidence/regressions/cognitive_awakening_v33_canonical.json")
    parser.add_argument("--json-out", type=Path, default=AUDIT_ROOT / "final_acceptance.json")
    args = parser.parse_args()
    checks: list[dict[str, Any]] = [
        {"name": "version", "passed": (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "3.3.0", "reason": "VERSION must be 3.3.0"},
        protected_contracts(),
        command_check("compile", [sys.executable, "-m", "compileall", "-q", "src", "scripts", "tests"]),
        command_check("schema_drift", [sys.executable, "scripts/export_schemas.py", "--check"]),
    ]
    if args.skip_tests:
        checks.append({"name": "pytest", "passed": True, "reason": "per-job pytest result must be present in CI matrix evidence"})
    else:
        checks.append(command_check("pytest", [sys.executable, "-m", "pytest", "-q"]))
    checks.extend([
        matrix_check(args.ci_evidence),
        real_codex_check(args.codex_evidence),
        regression_check("stock_operator_regression", args.stock_regression),
        regression_check("cognitive_awakening_regression", args.cognitive_regression),
        release_identity_check([
            args.ci_evidence, args.codex_evidence, args.stock_regression, args.cognitive_regression,
        ]),
        release_layout_check(),
    ])
    blockers = [item for item in checks if not item["passed"]]
    payload = {
        "schema_version": 1, "engine_version": "3.3.0",
        "generated_at": now(), "status": "passed" if not blockers else "blocked",
        "checks": checks,
        "blockers": [{"item": item["name"], "reason": item.get("reason", "failed")} for item in blockers],
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if blockers:
        print("V3.3.0_BLOCKED")
        for blocker in payload["blockers"]:
            print(f"- {blocker['item']}: {blocker['reason']}")
        return 33
    print("V3.3.0_PRODUCTION_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
