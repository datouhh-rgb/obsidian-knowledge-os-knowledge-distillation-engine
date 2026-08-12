from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_engine.exposure import SkillExposureManager
from knowledge_engine.models import GroundedStatement, QualityScore, SkillCandidate
from knowledge_engine.pipeline import DistillationPipeline
from knowledge_engine.registry import REGISTRY_JSON, SkillRegistry
from knowledge_engine.synthesis import PROPOSALS_JSON, SynthesisMaterializationService, SynthesisRegistry
from knowledge_engine.utils import atomic_write
from conftest import make_production_pipeline


def _policy(text: str) -> dict:
    return {"text": text, "evidence_ids": [], "origin": "engine_policy"}


def test_shallow_opposing_steps_are_quarantined_before_synthesis_registry(vault: Path, source_factory) -> None:
    first = source_factory(book="Book A", steps=["必须保存原件。", "记录行号。", "核对哈希。"])
    second = source_factory(book="Book B", steps=["不得保存原件。", "记录行号。", "核对哈希。"])
    make_production_pipeline(vault).distill(str(first), title="Book A")
    blocked = make_production_pipeline(vault).distill(str(second), title="Book B")
    registry = SkillRegistry.load(vault)
    assert len(registry.records) == 0
    assert blocked.status == "completed"
    assert any(item.code == "skill-candidate-quarantined" for item in blocked.validation.warnings)
    assert not SynthesisRegistry.load(vault).proposals


def test_duplicate_sources_do_not_auto_create_synthesis(vault: Path, source_factory) -> None:
    first = source_factory(book="Book A", suffix="A")
    second = source_factory(book="Book B", suffix="B")
    make_production_pipeline(vault).distill(str(first), title="Book A")
    blocked = make_production_pipeline(vault).distill(str(second), title="Book B")
    assert blocked.status == "blocked"
    assert not blocked.written_paths
    assert not SynthesisRegistry.load(vault).proposals


def test_new_version_usage_starts_empty_and_old_content_remains_complete(distilled) -> None:
    registry = SkillRegistry.load(distilled["vault"])
    record, v1, lifecycle1 = registry.version(distilled["skill"], "1.0.0")
    lifecycle1["usage"]["invocation_count"] = 7
    lifecycle1["usage"]["outcomes"]["success"] = 7
    lifecycle1["status"] = "approved"
    candidate = SkillCandidate.from_payload(v1["content"])
    score_data = v1["quality_at_creation"]
    score = QualityScore(
        score_data["provenance_integrity"], score_data["semantic_support"], score_data["executability"],
        None, None, 0, 100, None, None, 100, 90, "pass", (),
    )
    sources = [
        {"source_id": "a" * 64, "conversion_id": "b" * 64, "evidence_ids": []},
        {"source_id": "c" * 64, "conversion_id": "d" * 64, "evidence_ids": []},
    ]
    item = registry.create_synthesis_version(record["canonical_name"], record["title"], candidate, sources, score, [], "proposal-test", {"provider": "test"})
    _, v2, lifecycle2 = registry.version(record["canonical_name"], item.version)
    assert lifecycle2["usage"]["invocation_count"] == 0
    assert lifecycle2["usage"]["outcomes"] == {"success": 0, "partial": 0, "failed": 0}
    assert v1["content"] == candidate.semantic_payload()
    assert v1 is not v2
    lifecycle2["status"] = "approved"
    event = registry.rollback(record["canonical_name"], "1.0.0", "human", "restore")
    assert event["to"] == "1.0.0"
    assert record["current_version"] == "1.0.0"
    assert "--- 1.0.0" in registry.compare(record["canonical_name"], "1.0.0", item.version)
