from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.run_v33_real_codex_integration import validate_skills_list_response


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/v3.3-knowledge-synthesis.yml"


def test_azw3_regression_does_not_copy_stale_snapshot() -> None:
    script = (ROOT / "scripts/run_stock_operator_regression.py").read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "--conversion-snapshot" not in script
    assert "--conversion-snapshot" not in workflow
    assert "copy_code" not in script
    assert "--kindleunpack-script" in script
    assert '"{input}", "{output_dir}"' in script


def test_real_codex_rebuilds_canonical_text_without_publishing_snapshot() -> None:
    script = (ROOT / "scripts/run_v33_real_codex_integration.py").read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "conversion_snapshot_path" not in script
    assert "reconstruct_canonical_source" in script
    assert "copyrighted_snapshot_published" in script
    real_codex = workflow.split("real-codex-windows-e2e:", 1)[1].split("release-assembly:", 1)[0]
    assert "KINDLEUNPACK_SCRIPT_B64" in real_codex
    assert '--kindleunpack-script "$kindleUnpack"' in real_codex


def test_real_codex_discovery_uses_official_force_reloaded_catalog() -> None:
    script = (ROOT / "scripts/run_v33_real_codex_integration.py").read_text(encoding="utf-8")
    revalidator = (ROOT / "scripts/revalidate_v33_codex_audit.py").read_text(encoding="utf-8")
    assert '"skills/list"' in script
    assert '"forceReload": True' in script
    assert '"scope") == "repo"' in script
    assert '"enabled") is True' in script
    assert 'run_codex_catalog_discovery' in script
    assert 'resolve_app_server_executable' in script
    assert 'revalidate_skills_list' in revalidator
    assert 'matching_entry_count' in revalidator


def test_concurrent_real_codex_sessions_isolate_writable_state() -> None:
    script = (ROOT / "scripts/run_v33_real_codex_integration.py").read_text(encoding="utf-8")
    assert 'session_home = codex_home / "sessions"' in script
    assert '"CODEX_HOME": str(session_home)' in script
    assert '"HOME": str(session_home)' in script
    assert '"USERPROFILE": str(session_home)' in script
    assert 'shutil.copy2(codex_home / "auth.json", session_home / "auth.json")' in script
    assert 'shutil.rmtree(session_home)' in script
    assert '"<PROMPT_ARGUMENT>"' in script
    assert 'subprocess.run(\n            [*command, prompt]' in script
    assert '"<PROMPT_STDIN>"' not in script
    assert 'version_record["artifact_path"]' in script
    assert 'duplicate_name_count' in script


def test_codex_catalog_rejects_nested_same_name_skill(tmp_path: Path) -> None:
    skill = "example-skill"
    description = "Example description"
    canonical = tmp_path / ".agents" / "skills" / skill / "SKILL.md"
    nested = canonical.parent / "versions" / "1.0.0" / "SKILL.md"
    response = {
        "result": {
            "data": [{
                "cwd": str(tmp_path),
                "errors": [],
                "skills": [
                    {
                        "name": skill,
                        "description": description,
                        "scope": "repo",
                        "enabled": True,
                        "path": str(canonical),
                    },
                    {
                        "name": skill,
                        "description": description,
                        "scope": "repo",
                        "enabled": True,
                        "path": str(nested),
                    },
                ],
            }],
        },
    }
    result = validate_skills_list_response(response, tmp_path, skill, description)
    assert result["passed"] is False
    assert result["matching_entry_count"] == 2
    assert result["canonical_entry_count"] == 1
    assert result["duplicate_name_count"] == 1


def test_workflow_uploads_blocked_book_evidence() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    book_section = workflow.split("book-regression-windows:", 1)[1].split("real-codex-windows-e2e:", 1)[0]
    assert "continue-on-error: true" in book_section
    assert "Ensure both book reports exist even when blocked" in book_section
    assert "if: always()" in book_section
    assert "canonical-book-regressions" in book_section
    assert "Aggregate book acceptance after evidence upload" in book_section


def test_book_regressions_use_fresh_disposable_vaults() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    book_section = workflow.split("book-regression-windows:", 1)[1].split("real-codex-windows-e2e:", 1)[0]
    assert "--work-dir" not in book_section
    assert "stock-final" not in book_section
    assert "cognitive-final" not in book_section


def test_cognitive_report_exists_when_stock_blocked() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    book_section = workflow.split("book-regression-windows:", 1)[1].split("real-codex-windows-e2e:", 1)[0]
    cognitive = book_section.split("Cognitive Awakening canonical regression", 1)[1]
    assert "if: always()" in cognitive.split("Ensure both book reports exist", 1)[0]
    assert "cognitive_awakening_v33_canonical.json" in cognitive


def test_real_codex_not_run_is_machine_readable(tmp_path: Path) -> None:
    report = tmp_path / "real-codex.json"
    completed = subprocess.run([
        sys.executable,
        str(ROOT / "scripts/ensure_v33_machine_evidence.py"),
        "--path", str(report),
        "--component", "real-codex-explicit-only",
        "--step-outcome", "not_run",
        "--upstream-reason", "Book regression gate did not pass.",
    ], cwd=ROOT, check=False)
    assert completed.returncode == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "not_run"
    assert payload["result"] == "NOT_RUN"
    assert payload["reason"] == "Book regression gate did not pass."


def test_final_publication_requires_all_gates() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    publication = workflow.split("final-publication:", 1)[1]
    assert "needs: [release-assembly, archive-verification]" in publication
    assert "aggregate_v33_archive_verification.py" in publication
    release = workflow.split("release-assembly:", 1)[1].split("archive-verification:", 1)[0]
    assert "Final fail-closed acceptance" in release
    assert "Preserve fail-closed release result" in release
    assert "v3.3.0-blocked-audit" in release


def test_archive_verification_uploads_failure_evidence() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    archive = workflow.split("archive-verification:", 1)[1].split("final-publication:", 1)[0]
    upload = archive.split("- uses: actions/upload-artifact@v4", 1)[1]
    assert "if: always()" in upload
    assert ".ci-evidence/archive-verification.json" in upload


def test_archive_verification_uses_cross_shell_github_identity_expressions() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    archive = workflow.split("archive-verification:", 1)[1].split("final-publication:", 1)[0]
    verify = archive.split("Extract and verify exact final ZIP bytes", 1)[1].split(
        "- uses: actions/upload-artifact@v4", 1,
    )[0]
    assert '--expected-commit "${{ github.sha }}"' in verify
    assert '--workflow-url "https://github.com/${{ github.repository }}/actions/runs/${{ github.run_id }}"' in verify
    assert "$COMMIT_SHA" not in verify
    assert "$RUN_URL" not in verify


def test_release_assembly_restores_multi_root_codex_artifact_at_repo_root() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    release = workflow.split("release-assembly:", 1)[1].split("archive-verification:", 1)[0]
    download = release.split("name: real-codex-explicit-only", 1)[1].split(
        "- name: Aggregate one machine matrix source", 1,
    )[0]
    assert "path: ." in download
    assert "path: release" not in download
