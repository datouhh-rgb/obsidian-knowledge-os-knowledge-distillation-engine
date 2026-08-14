from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from knowledge_engine.evidence import EvidenceCatalog
from knowledge_engine.knowledge_synthesis import KnowledgeSynthesizer, SkillSynthesizer
from knowledge_engine.models import (
    CanonicalConcept,
    ConceptCandidate,
    ConversionIdentity,
    GroundedStatement,
    IngestedSource,
    MethodCluster,
    ProviderResult,
)
from knowledge_engine.pipeline import DistillationPipeline
from knowledge_engine.quality import SkillQualityEvaluator
from knowledge_engine.registry import SkillRegistry
from knowledge_engine.semantic import SemanticSupportGate
from knowledge_engine.utils import sha256_text


class StockOperatorSynthesisProvider:
    name = "llm:stock-operator-synthesis-fixture"

    TITLES = {
        "判断风险扩大": "风险扩大控制",
        "执行止损纪律": "止损纪律控制",
        "控制仓位": "仓位风险控制",
        "观察价格反馈": "价格反馈观察",
        "分析趋势变化": "趋势变化分析",
        "核验市场判断": "市场判断核验",
        "复盘交易记录": "交易记录复盘",
        "检查执行偏差": "执行偏差复盘检查",
        "记录市场反馈": "反馈记录修正",
    }

    def propose(self, analysis, catalog):  # type: ignore[no-untyped-def]
        concepts = []
        for evidence in catalog.items.values():
            title = next((value for key, value in self.TITLES.items() if key in evidence.excerpt), None)
            if title:
                concepts.append(ConceptCandidate(
                    title, GroundedStatement(evidence.excerpt, (evidence.evidence_id,), "source"),
                    self.name, 0.92,
                ))
        return ProviderResult(
            provider=self.name,
            concepts=concepts,
            provenance={"provider": self.name, "fixture": "stock-operator-v3.3"},
        )


@pytest.fixture
def stock_operator_synthesis_fixture() -> tuple[EvidenceCatalog, list[CanonicalConcept]]:
    text = """# 风险与纪律

判断风险扩大时，应当停止增加暴露并重新检查退出条件。

执行止损纪律可以限制单次错误继续扩大。

控制仓位之前要比较潜在损失和可承受范围。

# 市场观察

观察价格反馈需要区分自己的判断和市场已经发生的事实。

分析趋势变化时，应比较连续反馈而不是依赖一次波动。

核验市场判断要记录预期信号和实际出现的信号。

# 复盘改进

复盘交易记录可以识别重复出现的判断错误。

检查执行偏差时要比较计划、行动和最终结果。

记录市场反馈有助于修正下一次决策条件。
"""
    catalog = EvidenceCatalog.from_text(
        "股票大作手回忆录", "source-stock-operator", text, "conversion-stock-operator"
    )
    specifications = [
        ("风险扩大控制", "判断风险扩大时，应当停止增加暴露并重新检查退出条件。", "risk"),
        ("止损纪律控制", "执行止损纪律可以限制单次错误继续扩大。", "risk"),
        ("仓位风险控制", "控制仓位之前要比较潜在损失和可承受范围。", "risk"),
        ("价格反馈观察", "观察价格反馈需要区分自己的判断和市场已经发生的事实。", "assessment"),
        ("趋势变化分析", "分析趋势变化时，应比较连续反馈而不是依赖一次波动。", "assessment"),
        ("市场判断核验", "核验市场判断要记录预期信号和实际出现的信号。", "assessment"),
        ("交易记录复盘", "复盘交易记录可以识别重复出现的判断错误。", "review"),
        ("执行偏差复盘检查", "检查执行偏差时要比较计划、行动和最终结果。", "review"),
        ("反馈记录修正", "记录市场反馈有助于修正下一次决策条件。", "review"),
    ]
    by_excerpt = {item.excerpt: item.evidence_id for item in catalog.items.values()}
    concepts = [
        CanonicalConcept(
            canonical_concept_id="concept:" + sha256_text(title)[:24],
            canonical_title=title,
            aliases=(title,),
            source_mentions=(definition,),
            evidence_ids=(by_excerpt[definition],),
            merge_reason="stable regression fixture",
            confidence=0.92,
            definition=GroundedStatement(definition, (by_excerpt[definition],), "source"),
            provider=f"llm:fixture:{purpose}",
        )
        for title, definition, purpose in specifications
    ]
    return catalog, concepts


def test_concept_to_method_cluster(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    assert result.method_clusters
    assert all(len(item.concept_ids) >= 2 for item in result.method_clusters)
    assert all("semantic_similarity=" in item.creation_reason for item in result.method_clusters)
    assert all("shared_chapter_pairs=" in item.creation_reason for item in result.method_clusters)


@pytest.mark.parametrize("title", ["学习只", "阿健同学正", "在生活中更"])
def test_real_pdf_truncated_concept_never_enters_method_synthesis(
    title: str,
    stock_operator_synthesis_fixture,
) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    fragment = replace(concepts[0], canonical_title=title)
    assert KnowledgeSynthesizer()._profile(fragment, catalog) is None


def test_method_anchor_selection_rejects_containment_duplicates() -> None:
    anchors = KnowledgeSynthesizer._distinct_anchors([
        "极度专注与主动休息",
        "主动休息",
        "注意力恢复",
    ])
    assert anchors == ["极度专注与主动休息", "注意力恢复"]


def test_method_cluster_evidence_union(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    by_id = {item.canonical_concept_id: item for item in concepts}
    for cluster in result.method_clusters:
        expected = {
            evidence_id
            for concept_id in cluster.concept_ids
            for evidence_id in by_id[concept_id].evidence_ids
        }
        assert set(cluster.evidence_ids) == expected


def test_skill_synthesis_requires_method(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, _concepts = stock_operator_synthesis_fixture
    outcome = SkillSynthesizer().synthesize(None, catalog)
    assert outcome.candidate is None
    assert any(item.code == "skill-synthesis-method-required" for item in outcome.validation.errors)


def test_skill_candidate_quality_gate(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    weak = MethodCluster(
        id="method-cluster:weak",
        title="趋势知识",
        description="insufficient",
        concept_ids=(concepts[0].canonical_concept_id,),
        evidence_ids=concepts[0].evidence_ids,
        source_books=("股票大作手回忆录",),
        confidence=0.9,
        creation_reason="single title",
    )
    outcome = SkillSynthesizer().synthesize(weak, catalog)
    assert outcome.candidate is None
    codes = {item.code for item in outcome.validation.errors}
    assert {"method-cluster-concepts", "method-cluster-evidence"}.issubset(codes)


def test_stock_operator_skill_synthesis_regression(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    assert 3 <= len(result.skills) <= 8
    for skill in result.skills:
        assert len({item for statement in skill.source_statements() for item in statement.evidence_ids}) >= 3
        assert skill.fingerprint() in result.skill_method_ids


def test_skill_not_auto_approve(tmp_path: Path, stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    synthesis = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    candidate = synthesis.skills[0]
    assessments = SemanticSupportGate().assess_skill(candidate, catalog).assessments
    score = SkillQualityEvaluator().score(candidate, catalog, {}, assessments)
    original = tmp_path / "股票大作手回忆录.md"
    original.write_text(catalog.source_text, encoding="utf-8")
    source = IngestedSource(
        input_ref=str(original), local_path=original, original_name=original.name,
        media_type="text/markdown", source_kind="markdown", sha256=catalog.source_id,
        text=catalog.source_text, identity_source_id=catalog.source_id,
    )
    conversion = ConversionIdentity(
        catalog.source_id, catalog.conversion_id, sha256_text(catalog.source_text),
        "fixture", "1", {}, (),
    )
    registry = SkillRegistry.empty()
    materialized = registry.create_source_candidate(
        candidate, source, conversion, "股票大作手回忆录", score, assessments,
        {"provider": candidate.provider},
    )
    _record, _version, lifecycle = registry.version(materialized.name, materialized.version)
    assert lifecycle["status"] == "candidate"
    assert lifecycle["status"] != "approved"


def test_v33_pipeline_writes_method_graph_and_review_only_skills(
    tmp_path: Path,
    vault: Path,
    stock_operator_synthesis_fixture,
) -> None:  # type: ignore[no-untyped-def]
    catalog, _concepts = stock_operator_synthesis_fixture
    source = tmp_path / "股票大作手回忆录-合成回归.md"
    source.write_text(catalog.source_text, encoding="utf-8")
    result = DistillationPipeline(
        vault, provider=StockOperatorSynthesisProvider(), chunk_chars=30_000,
    ).distill(str(source), title="股票大作手回忆录")
    assert result.status == "completed"
    assert 3 <= len(result.method_clusters) <= 8

    methods = list((vault / "05_Knowledge" / "Methods").glob("*.md"))
    assert len(methods) >= len(result.method_clusters)
    assert all("## Concepts" in item.read_text(encoding="utf-8") for item in methods)

    graph = json.loads(
        (vault / "05_Knowledge" / "Graph" / "concept-graph.v3.json").read_text(encoding="utf-8")
    )
    edge_types = {item["type"] for item in graph["edges"].values()}
    assert {"supports", "generates"}.issubset(edge_types)

    registry = SkillRegistry.load(vault)
    assert 3 <= len(registry.records) <= 8
    for record in registry.records.values():
        version = record["current_version"]
        assert record["lifecycle"][version]["status"] == "candidate"
        assert record["lifecycle"][version]["status"] != "approved"
    assert not (vault / ".agents" / "skills").exists()
