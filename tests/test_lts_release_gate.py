from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.final_lts_check import (
    CheckResult,
    REQUIRED_CHECKS,
    REQUIRED_MATRIX,
    cross_platform_check,
    lts_decision,
)


def result(name: str, passed: bool = True) -> CheckResult:
    return CheckResult(name, passed, "test", 0 if passed else 1, "test", "repair")


@pytest.mark.parametrize("failed_name", REQUIRED_CHECKS)
def test_every_required_lts_check_is_fail_closed(failed_name: str) -> None:
    results = [result(name, name != failed_name) for name in REQUIRED_CHECKS]
    status, blockers = lts_decision(results)
    assert status == "V3.1_LTS_BLOCKED"
    assert f"failed:{failed_name}" in blockers


def test_missing_lts_check_is_blocked() -> None:
    status, blockers = lts_decision([result(name) for name in REQUIRED_CHECKS[:-1]])
    assert status == "V3.1_LTS_BLOCKED"
    assert blockers == [f"missing:{REQUIRED_CHECKS[-1]}"]


def test_all_required_lts_checks_can_be_ready() -> None:
    status, blockers = lts_decision([result(name) for name in REQUIRED_CHECKS])
    assert status == "V3.1_LTS_READY"
    assert blockers == []


def test_cross_platform_evidence_requires_exact_six_job_matrix(tmp_path: Path) -> None:
    evidence = tmp_path / "ci.json"
    jobs = [
        {
            "os": system, "python": version, "pytest_exit_code": 0,
            "exposure_exit_code": 0, "migration_exit_code": 0, "path_exit_code": 0,
            "raw_log_sha256": "a" * 64,
        }
        for system, version in sorted(REQUIRED_MATRIX)
    ]
    evidence.write_text(json.dumps({
        "schema_version": 1, "workflow_run_id": "123", "commit_sha": "b" * 40, "jobs": jobs,
    }), encoding="utf-8")
    assert cross_platform_check(evidence).passed

    jobs.pop()
    evidence.write_text(json.dumps({
        "schema_version": 1, "workflow_run_id": "123", "commit_sha": "b" * 40, "jobs": jobs,
    }), encoding="utf-8")
    assert not cross_platform_check(evidence).passed
