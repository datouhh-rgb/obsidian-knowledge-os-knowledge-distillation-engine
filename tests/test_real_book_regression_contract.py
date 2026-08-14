from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def test_cognitive_awakening_original_pdf_end_to_end() -> None:
    report_value = os.environ.get("V33_COGNITIVE_REGRESSION_REPORT", "")
    if not report_value:
        pytest.skip("Real PDF regression is executed by the self-hosted acceptance job.")
    report = Path(report_value)
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["book"] == "认知觉醒"
    assert payload["mode"] == "real-pdf-markitdown-codex-llm-disposable-resumable-vault"
    assert payload["pdf_converter"] == "Microsoft MarkItDown"
    assert payload["conversion_quality"]["status"] == "passed"
    assert payload["checks"]["source_unchanged"] is True
    assert payload["checks"]["portable_evidence_selection"] is True
    assert payload["checks"]["no_graph_identity_collision"] is True
    assert payload["status"] == "passed"
