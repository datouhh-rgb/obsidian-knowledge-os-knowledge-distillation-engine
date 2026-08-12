from __future__ import annotations

from knowledge_engine.evidence import EvidenceCatalog
from knowledge_engine.knowledge_synthesis import KnowledgeSynthesizer
from knowledge_engine.models import CanonicalConcept, GroundedStatement
from knowledge_engine.utils import sha256_text
from test_v33_knowledge_synthesis import stock_operator_synthesis_fixture


def _concepts(text: str, specifications: list[tuple[str, str]]) -> tuple[EvidenceCatalog, list[CanonicalConcept]]:
    catalog = EvidenceCatalog.from_text("凝聚度测试", "source-cohesion", text, "conversion-cohesion")
    concepts = []
    for title, phrase in specifications:
        evidence = next(item for item in catalog.items.values() if phrase in item.excerpt)
        concepts.append(CanonicalConcept(
            canonical_concept_id="concept:" + sha256_text(title)[:24],
            canonical_title=title,
            aliases=(title,),
            source_mentions=(evidence.excerpt,),
            evidence_ids=(evidence.evidence_id,),
            merge_reason="fixture",
            confidence=0.9,
            definition=GroundedStatement(evidence.excerpt, (evidence.evidence_id,), "source"),
            provider="llm:test",
        ))
    return catalog, concepts


def test_same_book_is_not_a_cohesion_signal() -> None:
    text = """# 天气\n判断天气变化并记录温度。\n# 信用\n评估信用水平并保存授信结论。\n# 饮食\n分析饮食结构并输出营养清单。\n"""
    catalog, concepts = _concepts(text, [
        ("天气变化判断", "天气变化"), ("信用水平评估", "信用水平"), ("饮食结构分析", "饮食结构"),
    ])
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "同一本书")
    assert not result.method_clusters
    assert any("insufficient multi-signal cohesion" in str(item["reasons"]) for item in result.blocked)


def test_method_cluster_has_explainable_shared_goal(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    assert result.method_clusters
    for cluster in result.method_clusters:
        assessment = cluster.cohesion_assessment
        assert assessment["decision"] == "passed"
        assert "semantic" in assessment["strong_signals"]
        assert len(assessment["strong_signals"]) >= 2
        assert assessment["goal"]
        assert assessment["concepts"]


def test_cluster_order_independence(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    forward = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    reverse = KnowledgeSynthesizer().synthesize(list(reversed(concepts)), catalog, "股票大作手回忆录")
    assert [item.as_dict() for item in forward.method_clusters] == [
        item.as_dict() for item in reverse.method_clusters
    ]
    assert [item.fingerprint() for item in forward.skills] == [
        item.fingerprint() for item in reverse.skills
    ]


def test_low_semantic_zero_chapter_cluster_is_blocked() -> None:
    text = """# 同一章节\n判断天气变化并记录温度。\n评估信用水平并保存授信结论。\n分析饮食结构并输出营养清单。\n"""
    catalog, concepts = _concepts(text, [
        ("天气变化判断", "天气变化"), ("信用水平评估", "信用水平"), ("饮食结构分析", "饮食结构"),
    ])
    assert not KnowledgeSynthesizer().synthesize(concepts, catalog, "同一本书").method_clusters


def test_hard_negative_concepts_do_not_cluster() -> None:
    text = """# 一\n检查天气温度并输出记录。\n# 二\n检查授信额度并输出记录。\n# 三\n检查膳食热量并输出记录。\n"""
    catalog, concepts = _concepts(text, [
        ("检查天气温度", "天气温度"), ("检查授信额度", "授信额度"), ("检查膳食热量", "膳食热量"),
    ])
    assert not KnowledgeSynthesizer().synthesize(concepts, catalog, "同一本书").method_clusters


def test_existing_stock_negative_candidate_has_machine_readable_reason() -> None:
    text = """# 一\n判断天气变化并记录温度。\n# 二\n评估信用水平并保存授信结论。\n# 三\n分析饮食结构并输出营养清单。\n"""
    catalog, concepts = _concepts(text, [
        ("天气变化判断", "天气变化"), ("信用水平评估", "信用水平"), ("饮食结构分析", "饮食结构"),
    ])
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    assert result.blocked
    assert all(isinstance(item.get("reasons"), list) and item["reasons"] for item in result.blocked)


def test_action_keyword_frequency_cannot_define_method_title(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    action_only = {"判断", "评估", "分析", "识别", "观察", "比较", "记录", "复盘", "检查", "执行"}
    for cluster in result.method_clusters:
        assert not all(token in cluster.title for token in action_only)
        titles = {item["title"] for item in cluster.cohesion_assessment["concepts"]}
        assert any(title in cluster.title for title in titles)


def test_english_provider_concepts_enter_existing_semantic_cohesion_gate() -> None:
    text = """# Trend assessment\nAssess and analyze the overall market trend before a trade.\n# Fact alignment\nObserve market facts and analyze alignment with the trend.\n# Price signals\nCompare and analyze price signals with the prevailing market trend.\n"""
    catalog, concepts = _concepts(text, [
        ("Overall-market-first analysis", "Assess and analyze the overall market trend"),
        ("Market trend alignment", "Observe market facts"),
        ("Price-signal comparison", "Compare and analyze price signals"),
    ])
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    assert result.method_clusters
    assert result.skills
    assert all(item.cohesion_assessment["decision"] == "passed" for item in result.method_clusters)
