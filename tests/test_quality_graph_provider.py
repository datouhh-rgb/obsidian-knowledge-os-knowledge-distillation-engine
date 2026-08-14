from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_engine.analyzer import BookAnalyzer
from knowledge_engine.evidence import EvidenceCatalog
from knowledge_engine.graph import GRAPH_JSON, ConceptGraph
from knowledge_engine.models import GroundedStatement, SkillCandidate
from knowledge_engine.pipeline import DistillationPipeline
from knowledge_engine.providers import HybridAnalyzerProvider, LLMAnalyzerProvider, RuleAnalyzerProvider
from knowledge_engine.quality import SkillQualityEvaluator
from knowledge_engine.registry import SkillRegistry
from knowledge_engine.semantic import SemanticSupportGate
from conftest import make_production_pipeline


class FakeTransport:
    name = "fake"
    model = "fake"
    config = {"temperature": 0}

    def __init__(self, payload): self.payload = payload
    def complete(self, request): return self.payload  # type: ignore[no-untyped-def]


def _candidate(text: str, evidence_id: str, origin: str = "source") -> SkillCandidate:
    source = GroundedStatement(text, (evidence_id,), origin)  # type: ignore[arg-type]
    policy = GroundedStatement("只按证据执行。", (), "engine_policy")
    return SkillCandidate("核验流程", policy, (policy,), (policy,), (policy, policy), (source, source), policy, (policy,), (policy,), "test", 0.8)


def test_unsupported_statement_fails_semantic_gate() -> None:
    catalog = EvidenceCatalog.from_text("T", "a" * 64, "# T\n\n苹果是红色。\n", "b" * 64)
    candidate = _candidate("量子纠缠决定企业利润。", next(iter(catalog.items)))
    result = SemanticSupportGate().assess_skill(candidate, catalog)
    assert result.status == "failed"
    assert any(item.code == "semantic-unsupported" for item in result.report.errors)


def test_partial_and_conflict_require_human_review() -> None:
    catalog = EvidenceCatalog.from_text("T", "a" * 64, "# T\n\n必须保留现金。\n\n不得保留现金。\n", "b" * 64)
    ids = tuple(catalog.items)
    statement = GroundedStatement("保留现金。", ids)
    candidate = SkillCandidate("现金流程", GroundedStatement("定位", (), "engine_policy"), (GroundedStatement("场景", (), "engine_policy"),), (GroundedStatement("输入", (), "engine_policy"),), (GroundedStatement("判断", (), "engine_policy"),) * 2, (statement, statement), GroundedStatement("输出", (), "engine_policy"), (GroundedStatement("边界", (), "engine_policy"),), (GroundedStatement("禁止", (), "engine_policy"),), "test")
    result = SemanticSupportGate().assess_skill(candidate, catalog)
    assert result.status == "review"
    assert any(item.status == "conflict" for item in result.assessments)


def test_conversion_warning_lowers_quality_and_blocks_approval(distilled) -> None:
    registry = SkillRegistry.load(distilled["vault"])
    record, version, _ = registry.version(distilled["skill"], "1.0.0")
    candidate = SkillCandidate.from_payload(version["content"])
    conversion_root = next((distilled["result"].source_root / "conversions").iterdir())
    catalog = EvidenceCatalog.load(conversion_root / "EVIDENCE_CATALOG.json", conversion_root / "SOURCE_TEXT.md")
    assessments = SemanticSupportGate().assess_skill(candidate, catalog).assessments
    score = SkillQualityEvaluator().score(candidate, catalog, {}, assessments, ["OCR uncertain"])
    assert score.conversion_confidence < 100
    assert score.status == "review"


def test_audited_image_omission_preserves_text_evidence_confidence(distilled) -> None:
    registry = SkillRegistry.load(distilled["vault"])
    _, version, _ = registry.version(distilled["skill"], "1.0.0")
    candidate = SkillCandidate.from_payload(version["content"])
    conversion_root = next((distilled["result"].source_root / "conversions").iterdir())
    catalog = EvidenceCatalog.load(conversion_root / "EVIDENCE_CATALOG.json", conversion_root / "SOURCE_TEXT.md")
    assessments = SemanticSupportGate().assess_skill(candidate, catalog).assessments

    score = SkillQualityEvaluator().score(candidate, catalog, {}, assessments, [
        "EPUB contains 21 declared image omission(s).",
        "21 image reference(s) were declared as omitted; no visual claims were inferred.",
    ])

    assert score.conversion_confidence == 100
    assert score.status == "pass"


def test_high_duplicate_fails_closed_before_registry(vault: Path, source_factory) -> None:
    one = source_factory(suffix="A")
    two = source_factory(suffix="B")
    make_production_pipeline(vault).distill(str(one), title="Book A")
    result = make_production_pipeline(vault).distill(str(two), title="Book B")
    registry = SkillRegistry.load(vault)
    assert len(registry.records) == 1
    assert result.status == "blocked"
    assert not result.written_paths
    assert not result.synthesis_proposals


def test_rule_provider_is_diagnostic_only() -> None:
    text = "# T\n\n## 核验流程\n\n1. 保存原件。\n2. 核对哈希。\n"
    analysis = BookAnalyzer().analyze(text, "T")
    catalog = EvidenceCatalog.from_text("T", "a" * 64, text, "b" * 64)
    result = RuleAnalyzerProvider().propose(analysis, catalog)
    assert not result.skills
    assert result.provenance["mode"] == "diagnostic-only"


def test_hybrid_uses_llm_skill_not_rule_skill() -> None:
    text = "# T\n\n## 核验流程\n\n1. 保存原件。\n2. 核对哈希。\n"
    analysis = BookAnalyzer().analyze(text, "T")
    catalog = EvidenceCatalog.from_text("T", "a" * 64, text, "b" * 64)
    st = lambda value, index: {"text": value, "selected_evidence_indexes": [index]}
    payload = {"concepts": [], "relations": [], "skills": [{
        "title": "核验流程", "positioning": st("保存原件。", 1), "scenarios": [st("保存原件。", 1)],
        "input_requirements": [st("保存原件。", 1)], "decision_flow": [st("保存原件。", 1), st("核对哈希。", 2)],
        "actions": [st("保存原件。", 1), st("核对哈希。", 2)], "output_format": st("核对哈希。", 2),
        "boundaries": [st("保存原件。", 1)], "prohibited_scenarios": [st("核对哈希。", 2)], "confidence": 0.9,
    }]}
    result = HybridAnalyzerProvider(LLMAnalyzerProvider(FakeTransport(payload))).propose(analysis, catalog)
    assert len(result.skills) == 1
    assert not result.comparisons
    assert result.skills[0].provider.startswith("llm:")


def test_graph_rejects_malformed_and_dangling(vault: Path) -> None:
    path = vault / GRAPH_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 3, "engine_version": "3.0.0", "revision": 0, "nodes": {}, "edges": {"e": {}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        ConceptGraph.load(vault)
    graph = ConceptGraph.empty()
    graph.data["edges"]["edge:x"] = {
        "id": "edge:x", "from": "missing", "to": "missing2", "type": "similar", "status": "candidate",
        "provider": "llm", "confidence": 0.5, "source_id": "", "conversion_id": "", "evidence_ids": [],
        "created_at": "now", "approved_by": None, "version": None,
    }
    path.write_text(graph.to_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="引用"):
        ConceptGraph.load(vault)


def test_graph_schema_rejects_unknown_node_fields(vault: Path) -> None:
    graph = ConceptGraph.empty()
    graph.nodes["concept:x"] = {
        "id": "concept:x", "type": "concept", "title": "X", "created_at": "now",
        "aliases": ["X"], "unreviewed_payload": "must-not-pass",
    }
    path = vault / GRAPH_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(graph.to_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        ConceptGraph.load(vault)


def test_candidate_llm_edge_not_rendered_as_fact() -> None:
    graph = ConceptGraph.empty()
    graph.nodes["a"] = {"id": "a", "type": "concept", "title": "A", "created_at": "now", "aliases": ["A"]}
    graph.nodes["b"] = {"id": "b", "type": "concept", "title": "B", "created_at": "now", "aliases": ["B"]}
    graph.add_relation("a", "b", "similar", "candidate", "llm", 0.5, [])
    assert "A →" not in graph.to_markdown()
