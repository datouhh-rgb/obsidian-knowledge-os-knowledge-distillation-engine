from __future__ import annotations

from pathlib import Path

from scripts.vault_lock_stress import run_stress


def test_vault_lock_contention_completes_twenty_rounds(tmp_path: Path) -> None:
    result = run_stress(tmp_path / "vault", rounds=20)
    assert result["status"] == "passed", result
    assert result["transactions"] == 40
    assert result["observed_revision"] == 40
    assert result["failure_count"] == 0
    assert result["retained_audit_journal_count"] == 40
    assert result["incomplete_journals"] == []
    assert result["leaked_staging"] == []
