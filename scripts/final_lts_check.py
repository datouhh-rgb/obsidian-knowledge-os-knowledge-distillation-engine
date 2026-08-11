#!/usr/bin/env python3
"""Fail-closed V3.1.1 LTS production check."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "FINAL_LTS_AUDIT"
REPORT = ROOT / "FINAL_LTS_REPORT.md"
PYTHON = sys.executable

REQUIRED_CHECKS = (
    "manifest_hash",
    "compile_check",
    "pytest",
    "cross_platform",
    "evidence_validation",
    "skill_validation",
    "migration_dry_run_apply_rollback",
    "exposure_create_discover_remove_recreate",
    "rollback_test",
)

REQUIRED_MATRIX = {
    (system, version)
    for system in ("windows-latest", "ubuntu-latest", "macos-latest")
    for version in ("3.11", "3.12")
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    command: str
    exit_code: int
    reason: str
    suggestion: str
    stdout_path: str | None = None
    stderr_path: str | None = None
    stdout_sha256: str | None = None
    stderr_sha256: str | None = None


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def lts_decision(results: Iterable[CheckResult]) -> tuple[str, list[str]]:
    by_name = {item.name: item for item in results}
    missing = [name for name in REQUIRED_CHECKS if name not in by_name]
    failed = [name for name in REQUIRED_CHECKS if name in by_name and not by_name[name].passed]
    blockers = [f"missing:{name}" for name in missing] + [f"failed:{name}" for name in failed]
    return ("V3.1_LTS_READY" if not blockers else "V3.1_LTS_BLOCKED", blockers)


def _included_files() -> list[Path]:
    excluded_parts = {"__pycache__", ".pytest_cache", "FINAL_LTS_AUDIT", "FINAL_AUDIT_PACK"}
    excluded_names = {".coverage", "FINAL_LTS_REPORT.md"}
    return sorted(
        path for path in ROOT.rglob("*")
        if path.is_file()
        and not excluded_parts.intersection(path.relative_to(ROOT).parts)
        and path.name not in excluded_names
    )


def manifest_check() -> CheckResult:
    AUDIT.mkdir(parents=True, exist_ok=True)
    manifest = AUDIT / "MANIFEST.sha256"
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(ROOT).as_posix()}"
        for path in _included_files()
    ]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    failures: list[str] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        target = ROOT / relative
        if not target.is_file():
            failures.append(f"missing:{relative}")
        elif hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            failures.append(f"hash:{relative}")
    return CheckResult(
        "manifest_hash", not failures, "generate and verify FINAL_LTS_AUDIT/MANIFEST.sha256",
        0 if not failures else 1,
        f"{len(lines)} files verified" if not failures else "; ".join(failures),
        "Restore the affected release file and regenerate the manifest.",
        stdout_path="FINAL_LTS_AUDIT/MANIFEST.sha256",
        stdout_sha256=sha256_bytes(manifest.read_bytes()),
    )


def run_check(name: str, arguments: list[str], suggestion: str, *, timeout: int = 900) -> CheckResult:
    AUDIT.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONPATH"] = str(ROOT / "src")
    command = subprocess.list2cmdline(arguments)
    try:
        completed = subprocess.run(
            arguments, cwd=ROOT, env=environment, capture_output=True, timeout=timeout,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
        summary_lines = [line.strip() for line in stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
        summary = summary_lines[-1] if summary_lines else "command completed successfully"
        reason = summary if exit_code == 0 else f"command exited with {exit_code}: {summary}"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        exit_code = 124
        reason = f"command exceeded {timeout} seconds"
    stdout_path = AUDIT / f"{name}.stdout.txt"
    stderr_path = AUDIT / f"{name}.stderr.txt"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    return CheckResult(
        name, exit_code == 0, command, exit_code, reason, suggestion,
        stdout_path.relative_to(ROOT).as_posix(), stderr_path.relative_to(ROOT).as_posix(),
        sha256_bytes(stdout), sha256_bytes(stderr),
    )


def cross_platform_check(evidence_path: Path | None) -> CheckResult:
    suggestion = (
        "Run the official GitHub Actions six-job matrix on Windows, Ubuntu, and macOS "
        "with Python 3.11/3.12, then pass its consolidated JSON evidence."
    )
    if evidence_path is None or not evidence_path.is_file():
        return CheckResult(
            "cross_platform", False, "verify --cross-platform-evidence", 1,
            "verified six-job CI evidence was not supplied", suggestion,
        )
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        jobs = payload.get("jobs", [])
        observed = {(str(item.get("os")), str(item.get("python"))) for item in jobs}
        job_pass = all(
            item.get("pytest_exit_code") == 0
            and item.get("exposure_exit_code") == 0
            and item.get("migration_exit_code") == 0
            and item.get("path_exit_code") == 0
            and bool(item.get("raw_log_sha256"))
            for item in jobs
        )
        passed = (
            payload.get("schema_version") == 1
            and observed == REQUIRED_MATRIX
            and len(jobs) == len(REQUIRED_MATRIX)
            and job_pass
            and bool(payload.get("workflow_run_id"))
            and bool(payload.get("commit_sha"))
        )
        reason = "six required platform/runtime jobs passed" if passed else "CI evidence is incomplete or contains a failed job"
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
        passed = False
        reason = f"cannot validate CI evidence: {type(exc).__name__}: {exc}"
    return CheckResult(
        "cross_platform", passed, f"verify {evidence_path}", 0 if passed else 1, reason, suggestion,
        stdout_path=str(evidence_path),
        stdout_sha256=sha256_bytes(evidence_path.read_bytes()) if evidence_path.is_file() else None,
    )


def write_report(results: list[CheckResult], status: str, blockers: list[str]) -> None:
    lines = [
        "# FINAL LTS REPORT", "", f"LTS status: `{status}`", "",
        f"Engine package version: `3.1.1`", "",
        f"Generated: `{now()}`", "",
        f"Local platform: `{platform.platform()}`", "",
        f"Python: `{platform.python_version()}`", "",
        "## Scope", "",
        "V3.1.1 is a stability-only patch. It adds no knowledge feature, database, module, data-model change, or lifecycle transition.", "",
        "## Check results", "", "| Check | Result | Exit | Reason |", "|---|---|---:|---|",
    ]
    for item in results:
        lines.append(f"| {item.name} | {'PASS' if item.passed else 'FAIL'} | {item.exit_code} | {item.reason} |")
    lines.extend(["", "## Failures and repair suggestions", ""])
    failed = [item for item in results if not item.passed]
    if failed:
        for item in failed:
            lines.extend([
                f"### {item.name}", "", f"Command: `{item.command}`", "",
                f"Reason: {item.reason}", "", f"Suggested repair: {item.suggestion}", "",
            ])
    else:
        lines.append("None.\n")
    if blockers:
        lines.extend(["## Decision blockers", "", *[f"- `{item}`" for item in blockers], ""])
    lines.extend([
        "## Known limitations", "",
        "- A local run cannot prove other operating systems; `V3.1_LTS_READY` requires consolidated evidence from all six CI jobs.",
        "- Windows directory links may use junction fallback when symbolic-link permission is unavailable.",
        "- macOS support is accepted only after its Python 3.11 and 3.12 CI jobs pass.", "",
        "## Stability changes", "",
        "- Unified symlink/junction/directory cleanup behind `remove_exposure_link()`.",
        "- Added create/discover/remove/recreate exposure regressions.",
        "- Added explicit fail-closed LTS checks for pytest, Evidence, Skill validation, migration, exposure, rollback, compilation, and manifest integrity.",
    ])
    REPORT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    (AUDIT / "CHECK_RESULTS.json").write_text(
        json.dumps({
            "schema_version": 1, "engine_version": "3.1.1", "generated_at": now(),
            "status": status, "blockers": blockers, "checks": [asdict(item) for item in results],
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--cross-platform-evidence", type=Path)
    value.add_argument("--self-test", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.self_test:
        results = [CheckResult(name, name != "pytest", "self-test", 1 if name == "pytest" else 0, "injected", "repair") for name in REQUIRED_CHECKS]
        status, _ = lts_decision(results)
        print(status)
        return 0 if status == "V3.1_LTS_READY" else 17

    results = [manifest_check()]
    results.append(run_check("compile_check", [PYTHON, "-m", "compileall", "-q", "src", "scripts"], "Fix the reported Python syntax/import error."))
    results.append(run_check("pytest", [PYTHON, "-m", "pytest", "-q"], "Fix every failed or skipped-required release test before retrying.", timeout=1200))
    results.append(cross_platform_check(args.cross_platform_evidence))
    results.append(run_check(
        "evidence_validation", [PYTHON, "-m", "pytest", "-q", "tests/test_identity_evidence.py", "tests/test_v31_acceptance_controls.py::test_asset_ledger_and_strict_schema_detect_metadata_tamper"],
        "Restore Evidence/ledger identity and hash consistency; do not weaken validation.",
    ))
    results.append(run_check(
        "skill_validation", [PYTHON, "-m", "pytest", "-q", "tests/test_exposure.py::test_generated_skill_passes_official_skill_creator_validator", "tests/test_synthesis_versioning.py::test_synthesis_requires_two_sources_and_human_approval"],
        "Repair Skill structure or gate ordering without bypassing Evidence or approval.",
    ))
    results.append(run_check(
        "migration_dry_run_apply_rollback", [PYTHON, "-m", "pytest", "-q", "tests/test_chunking_migration.py::test_v2_migration_dry_run_apply_rollback_and_idempotency"],
        "Fix migration planning/rollback while preserving all V2/V3 bytes.",
    ))
    results.append(run_check(
        "exposure_create_discover_remove_recreate", [PYTHON, "-m", "pytest", "-q", "tests/test_exposure_cross_platform.py", "tests/test_symlink_cleanup.py"],
        "Fix platform-specific link cleanup; never delete the canonical Vault Skill.",
    ))
    results.append(run_check(
        "rollback_test", [PYTHON, "-m", "pytest", "-q", "tests/test_lifecycle_transaction.py::test_fault_injection_rolls_back_all_core_writes", "tests/test_lifecycle_transaction.py::test_crash_wal_recovery_restores_consistent_state"],
        "Restore WAL before/after hash reconciliation and fail closed on ambiguous state.",
    ))
    status, blockers = lts_decision(results)
    write_report(results, status, blockers)
    if status == "V3.1_LTS_READY":
        print("SUCCESS:\nV3.1_LTS_READY")
        return 0
    print("V3.1_LTS_BLOCKED")
    for item in blockers:
        print(f"- {item}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
