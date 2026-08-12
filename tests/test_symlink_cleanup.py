from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from knowledge_engine.exposure import remove_exposure_link
from knowledge_engine.transaction import VaultTransaction


def test_remove_exposure_link_preserves_link_target(tmp_path: Path) -> None:
    target = tmp_path / "canonical-skill"
    target.mkdir()
    sentinel = target / "SKILL.md"
    sentinel.write_text("canonical", encoding="utf-8")
    link = tmp_path / "linked-skill"
    try:
        VaultTransaction._create_directory_link(link, target)
    except Exception as exc:
        if os.name == "nt" and ("privilege" in str(exc).lower() or "特权" in str(exc)):
            pytest.skip("Windows true-symlink privilege is unavailable; managed-cache is tested separately.")
        raise

    remove_exposure_link(link)

    assert not os.path.lexists(str(link))
    assert sentinel.read_text(encoding="utf-8") == "canonical"


def test_remove_exposure_link_explicit_directory_cleanup(tmp_path: Path) -> None:
    directory = tmp_path / "fixture-directory"
    directory.mkdir()
    (directory / "temporary.txt").write_text("fixture", encoding="utf-8")

    remove_exposure_link(directory)

    assert not directory.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction branch")
def test_remove_windows_junction_without_deleting_target(tmp_path: Path) -> None:
    target = tmp_path / "junction-target"
    target.mkdir()
    sentinel = target / "SKILL.md"
    sentinel.write_text("canonical", encoding="utf-8")
    junction = tmp_path / "junction-link"
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    remove_exposure_link(junction)

    assert not os.path.lexists(str(junction))
    assert sentinel.read_text(encoding="utf-8") == "canonical"
