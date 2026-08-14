from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.assemble_v33_release import READY_NAME
from scripts.generate_v33_release_reports import consistent_release_commit


ROOT = Path(__file__).resolve().parents[1]


def test_root_manifest_is_not_ambiguous() -> None:
    assert not (ROOT / "MANIFEST.sha256").exists()
    assert (ROOT / "GIT_SOURCE_MANIFEST.sha256").is_file()
    assert "release/MANIFEST.sha256" in (ROOT / "REVIEW_BRIEF.md").read_text(encoding="utf-8") or "exact final archive" in (ROOT / "REVIEW_BRIEF.md").read_text(encoding="utf-8")


def test_reviewer_brief_matches_current_release() -> None:
    brief = (ROOT / "REVIEW_BRIEF.md").read_text(encoding="utf-8")
    assert "V3.3.0" in brief
    assert "V3.1.1" not in brief
    assert "V3.3.0_PRODUCTION_READY" in brief
    assert "ConversionQualityGate" in brief


def test_final_zip_has_external_detached_hash() -> None:
    assembler = (ROOT / "scripts/assemble_v33_release.py").read_text(encoding="utf-8")
    assert READY_NAME in assembler
    assert '(ROOT / "release/ZIP_SHA256.txt").write_text' in assembler
    assert "zip_hash = sha256(output)" in assembler
    assert "DETACHED_SHA256" in assembler


def test_release_commit_matches_all_machine_evidence() -> None:
    commit = "a" * 40
    valid, observed = consistent_release_commit(*(
        {"commit_sha": commit} for _ in range(5)
    ))
    assert valid and observed == commit
    invalid, _ = consistent_release_commit(
        {"commit_sha": commit}, {"commit_sha": "b" * 40},
    )
    assert not invalid


def test_archive_contains_no_runtime_cache() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    forbidden = ("__pycache__", ".pytest_cache", ".coverage", ".knowledge-engine/", ".venv/")
    assert not [item for item in tracked if any(part in item for part in forbidden)]
