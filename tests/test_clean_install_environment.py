from __future__ import annotations

import os
import subprocess
import venv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _python(venv_root: Path) -> Path:
    if os.name == "nt":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def _run(command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    assert completed.returncode == 0, (
        f"command failed ({completed.returncode}): {subprocess.list2cmdline(command)}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return completed


def test_editable_install_runs_suite_and_local_production_check(tmp_path: Path) -> None:
    """Prove the release works from a clean venv without source-path injection."""

    venv_root = tmp_path / "clean-venv"
    outside = tmp_path / "outside-source-tree"
    outside.mkdir()
    venv.EnvBuilder(with_pip=True).create(venv_root)
    python = _python(venv_root)

    _run(
        [str(python), "-m", "pip", "install", "-e", str(PROJECT_ROOT)],
        cwd=outside,
        timeout=600,
    )
    _run(
        [
            str(python),
            "-c",
            "import knowledge_engine, scripts; "
            "assert knowledge_engine.__file__; assert scripts.__file__",
        ],
        cwd=outside,
        timeout=60,
    )
    _run(
        [
            str(python), "-m", "pytest", "-q",
            "--ignore=tests/test_clean_install_environment.py",
        ],
        cwd=PROJECT_ROOT,
        timeout=1200,
    )
    completed = _run(
        [
            str(python),
            str(PROJECT_ROOT / "scripts" / "final_production_check.py"),
            "--local-only",
            "--skip-tests",
        ],
        cwd=outside,
        timeout=300,
    )
    assert "V3.3_LOCAL_CHECK_PASS" in completed.stdout
