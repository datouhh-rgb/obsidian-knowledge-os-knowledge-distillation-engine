from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "release/evidence/codex_real_test/codex_real_integration.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@pytest.fixture(scope="session")
def real_codex_audit() -> dict[str, Any]:
    assert EVIDENCE.is_file(), (
        "Real Codex evidence is missing. Run scripts/run_v33_real_codex_integration.py; "
        "unit fixtures and mock discovery are not accepted."
    )
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert payload["mode"] == "real-codex-cli-disposable-vault-strict-transcript-revalidation-no-mocks"
    assert payload["status"] == "passed", payload.get("blockers")
    assert payload["codex_cli_version"].startswith("codex-cli ")
    assert payload["raw_evidence"]
    for item in payload["raw_evidence"]:
        path = ROOT / item["path"]
        assert path.is_file(), item
        assert _sha256(path) == item["sha256"], item
        assert path.stat().st_size == item["bytes"], item
    return payload
