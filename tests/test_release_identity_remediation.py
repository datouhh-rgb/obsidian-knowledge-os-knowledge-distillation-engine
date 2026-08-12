from __future__ import annotations

from scripts.aggregate_v33_release_evidence import EXPECTED, portable_jobs_valid, render_ci_markdown
from scripts.assemble_v33_release import release_origin
from scripts.generate_v33_release_reports import consistent_release_commit


def _jobs(commit: str) -> list[dict[str, object]]:
    return [
        {
            "os": os_name,
            "python": python,
            "commit_sha": commit,
            "job_status": "passed",
            "pytest_status": "passed",
            "schema_drift_status": "passed",
            "manifest_staging_status": "passed",
            "production_check_status": "passed",
        }
        for os_name, python in sorted(EXPECTED)
    ]


def test_all_release_evidence_uses_same_commit() -> None:
    commit = "a" * 40
    valid, observed = consistent_release_commit(*({"commit_sha": commit} for _ in range(4)))
    assert valid and observed == commit
    invalid, _ = consistent_release_commit({"commit_sha": commit}, {"commit_sha": "b" * 40})
    assert not invalid


def test_ci_markdown_matches_machine_matrix() -> None:
    commit = "a" * 40
    jobs = _jobs(commit)
    payload = {
        "status": "passed", "commit_sha": commit,
        "workflow_url": "https://github.example/actions/runs/1", "jobs": jobs,
    }
    report = render_ci_markdown(payload)
    assert portable_jobs_valid(jobs, commit)
    assert "- Status: `PASS`" in report
    assert report.count("| passed | passed | passed | passed |") == 6
    payload["status"] = "blocked"
    assert "- Status: `BLOCKED`" in render_ci_markdown(payload)


def test_real_codex_artifact_matches_release_commit() -> None:
    commit = "c" * 40
    valid, observed = consistent_release_commit(
        {"commit_sha": commit}, {"commit_sha": commit}, {"commit_sha": commit},
    )
    assert valid and observed == commit


def test_final_zip_originates_from_release_assembly_job() -> None:
    commit = "d" * 40
    origin = release_origin(commit, "https://github.example/actions/runs/2")
    assert origin["commit_sha"] == commit
    assert origin["assembly_job"] == "release-assembly"
    assert origin["assembly"] == "git-archive-plus-canonical-release-evidence"
