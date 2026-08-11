from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from knowledge_engine.chunking import ProviderMapReducer
from knowledge_engine.evidence import EvidenceCatalog
from knowledge_engine.evidence_selector import EvidenceScope, EvidenceSelectionError, EvidenceSelector
from knowledge_engine.graph import GRAPH_JSON
from knowledge_engine.ledger import ASSET_LEDGER_JSON
from knowledge_engine.models import Analysis, ConceptCandidate, GroundedStatement, ProviderResult
from knowledge_engine.pipeline import DistillationPipeline
from knowledge_engine.providers import AnalyzerProviderError, LLMAnalyzerProvider
from knowledge_engine.registry import REGISTRY_JSON


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "real_failures" / "case_b"


class PayloadTransport:
    name = "v321-fixture"
    model = "fixture-index-selection"
    config = {"temperature": 0}

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests: list[dict[str, object]] = []

    def complete(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return self.payload


def _catalog() -> EvidenceCatalog:
    text = "# Book\n\n## One\n\nalpha evidence.\n\n## Two\n\nbeta evidence.\n"
    return EvidenceCatalog.from_text("Book", "a" * 64, text, "b" * 64)


def _concept_payload(indexes: list[int]) -> dict[str, object]:
    return {
        "concepts": [{
            "title": "风险控制",
            "definition": {"text": "alpha evidence.", "selected_evidence_indexes": indexes},
            "confidence": 0.9,
        }],
        "relations": [],
        "skills": [],
    }


def test_llm_cannot_create_evidence_id() -> None:
    transport = PayloadTransport({"evidence_id": "ev-629"})
    with pytest.raises(AnalyzerProviderError, match="EvidenceSelectionError.*forbidden Evidence field"):
        LLMAnalyzerProvider(transport).propose(Analysis("Book"), _catalog())


def test_valid_evidence_index_selection() -> None:
    catalog = _catalog()
    transport = PayloadTransport(_concept_payload([1]))
    result = LLMAnalyzerProvider(transport).propose(Analysis("Book"), catalog)
    assert result.concepts[0].definition.evidence_ids == EvidenceSelector(catalog).select([1])
    request = transport.requests[0]
    assert "evidence_catalog" not in request
    assert request["prompt_version"] == "distillation-map-prompt-v3.2.1-index-selection"
    assert request["examples"] == {
        "invalid": {"evidence": "ev-629"},
        "valid": {"selected_evidence_indexes": [3, 7]},
    }


def test_invalid_evidence_index() -> None:
    with pytest.raises(AnalyzerProviderError, match="Evidence index does not exist"):
        LLMAnalyzerProvider(PayloadTransport(_concept_payload([9999]))).propose(Analysis("Book"), _catalog())


def test_cross_chunk_evidence_selection() -> None:
    catalog = _catalog()
    ordered = sorted(catalog.items.values(), key=lambda item: item.start_line)
    outside = ordered[-1]
    scope = EvidenceScope(
        catalog.source_id,
        catalog.conversion_id,
        chunk_start_line=1,
        chunk_end_line=outside.start_line - 1,
    )
    selector = EvidenceSelector(catalog, scope)
    index = next(item["index"] for item in selector.prompt_candidates() if item["locator"] == outside.locator)
    with pytest.raises(EvidenceSelectionError, match="outside the current chunk"):
        selector.validate([index])


def test_cross_conversion_evidence_selection() -> None:
    catalog = _catalog()
    original = next(iter(catalog.items.values()))
    rebound = replace(original, conversion_id="c" * 64)
    malformed = EvidenceCatalog(
        catalog.source_title,
        catalog.source_id,
        catalog.conversion_id,
        catalog.source_text,
        catalog.original_path,
        {rebound.evidence_id: rebound},
    )
    with pytest.raises(EvidenceSelectionError, match="outside the current conversion"):
        EvidenceSelector(malformed).validate([1])


def test_evidence_hash_mismatch_is_blocked() -> None:
    catalog = _catalog()
    original = next(iter(catalog.items.values()))
    tampered = replace(original, excerpt_sha256="0" * 64)
    malformed = EvidenceCatalog(
        catalog.source_title,
        catalog.source_id,
        catalog.conversion_id,
        catalog.source_text,
        catalog.original_path,
        {tampered.evidence_id: tampered},
    )
    with pytest.raises(EvidenceSelectionError, match="hash mismatch"):
        EvidenceSelector(malformed).validate([1])


def test_abnormal_evidence_count_is_blocked() -> None:
    with pytest.raises(EvidenceSelectionError, match="exceeds hard limit"):
        EvidenceSelector(_catalog()).validate(list(range(1, 102)))


def test_portable_provider_map_v3_schema() -> None:
    catalog = _catalog()
    evidence_id = next(iter(catalog.items))
    result = ProviderResult(
        "llm:v321-fixture",
        concepts=[ConceptCandidate(
            "风险控制",
            GroundedStatement("alpha evidence.", (evidence_id,)),
            "llm:v321-fixture",
            0.9,
        )],
    )
    portable = ProviderMapReducer.portable(result, catalog)
    assert portable["schema_version"] == "portable-provider-map-v3"
    assert "evidence_id" not in json.dumps(portable, ensure_ascii=False)
    portable["concepts"][0]["definition"]["evidence_id"] = "ev-629"  # type: ignore[index]
    with pytest.raises(EvidenceSelectionError, match="forbidden field"):
        ProviderMapReducer.validate_portable(portable)


def test_stock_operator_real_regression() -> None:
    fixture = json.loads((FIXTURE_ROOT / "fixture_ev629.json").read_text(encoding="utf-8"))
    encoded = "".join((FIXTURE_ROOT / "RUN.ev629.original.json.b64").read_text(encoding="ascii").split())
    original_bytes = base64.b64decode(encoded)
    assert hashlib.sha256(original_bytes).hexdigest() == fixture["original_run_sha256"]
    historical_run = json.loads(original_bytes)
    readable_bytes = (FIXTURE_ROOT / "RUN.ev629.original.json").read_bytes()
    assert hashlib.sha256(readable_bytes).hexdigest() == fixture["readable_copy_sha256"]
    assert json.loads(readable_bytes) == historical_run
    assert historical_run["run_id"] == "run-a80c81c42c1f319c7a9c50ff"
    assert historical_run["failed_chunk"] == fixture["failed_chunk"]
    assert historical_run["warnings"] == [fixture["error"]]

    catalog = _catalog()
    transport = PayloadTransport(_concept_payload([1]))
    result = LLMAnalyzerProvider(transport).propose(Analysis("股票大作手回忆录"), catalog)
    assert result.concepts
    assert all(catalog.get(item) is not None for item in result.concepts[0].definition.evidence_ids)
    assert "unknown Evidence" not in json.dumps(result.provenance, ensure_ascii=False)
    assert not any("unknown Evidence" in warning for warning in result.warnings)


def test_retry_does_not_commit_partial_asset(vault: Path, source_factory) -> None:
    transport = PayloadTransport(_concept_payload([9999]))
    before = {path.relative_to(vault).as_posix() for path in vault.rglob("*") if path.is_file()}
    result = DistillationPipeline(vault, provider=LLMAnalyzerProvider(transport)).distill(
        str(source_factory()), title="Evidence Retry Book",
    )
    after = {path.relative_to(vault).as_posix() for path in vault.rglob("*") if path.is_file()}
    assert result.status == "blocked"
    assert result.written_paths == []
    assert result.chunk_run and result.chunk_run["status"] == "blocked"
    assert len(transport.requests) == 3
    assert not (vault / GRAPH_JSON).exists()
    assert not (vault / REGISTRY_JSON).exists()
    assert not (vault / ASSET_LEDGER_JSON).exists()
    assert not any(path.startswith("05_Knowledge/Skills/") for path in after - before)

    manifest_path = (
        vault / ".knowledge-engine" / "runs" / "binding-runs"
        / result.chunk_run["run_id"] / "RUN.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "blocked"
    assert manifest["failed_chunk_status"] == "FAILED"
    assert manifest["failure"]["type"] == "EvidenceSelectionError"
    assert manifest["failure"]["retry_attempts"] == 3
