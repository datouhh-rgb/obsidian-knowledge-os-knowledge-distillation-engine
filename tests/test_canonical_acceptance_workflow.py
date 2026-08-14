from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


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
