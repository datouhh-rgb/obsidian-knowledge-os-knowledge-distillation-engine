from __future__ import annotations

from difflib import SequenceMatcher

from knowledge_engine.evidence import EvidenceCatalog
from knowledge_engine.gates import ProceduralActionGate
from knowledge_engine.knowledge_synthesis import KnowledgeSynthesizer, SkillSynthesizer
from knowledge_engine.models import MethodCluster
from knowledge_engine.semantic import SemanticSupportGate, statement_hash
from test_v33_knowledge_synthesis import stock_operator_synthesis_fixture


def test_signal_phrase_no_arbitrary_chinese_bigrams() -> None:
    signals = SkillSynthesizer._evidence_signal_terms(
        "形势变化以后需要重新观察价格反馈。",
        "趋势变化分析",
    )
    assert "断风" not in signals
    assert "势变" not in signals
    assert set(signals).issubset(set(SkillSynthesizer.EVIDENCE_SIGNAL_TERMS) | {"趋势变化分析"})


def test_signal_phrase_is_present_in_concept_or_evidence(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    clusters = {item.id: item for item in result.method_clusters}
    for skill in result.skills:
        cluster = clusters[result.skill_method_ids[skill.fingerprint()]]
        concept_titles = {
            item["title"] for item in cluster.cohesion_assessment["concepts"]
        }
        for action in skill.actions:
            evidence = " ".join(catalog.get(item).excerpt for item in action.evidence_ids if catalog.get(item))
            quoted = action.text.split("原文信号“", 1)[1].split("”", 1)[0].split("、")
            assert quoted
            assert all(item in evidence or item in concept_titles for item in quoted)


def test_insufficient_signal_blocks_candidate() -> None:
    text = "# 章节\n\n甲乙丙丁戊己庚辛。\n\n壬癸子丑寅卯辰巳。\n\n午未申酉戌亥天地。\n"
    catalog = EvidenceCatalog.from_text("无信号", "source", text, "conversion")
    evidence_ids = tuple(catalog.items)
    cluster = MethodCluster(
        id="method-cluster:insufficient",
        title="完整方法分析",
        description="用于验证信号不足时 fail closed。",
        concept_ids=("concept:one", "concept:two"),
        evidence_ids=evidence_ids,
        source_books=("无信号",),
        confidence=0.9,
        creation_reason="test",
        cohesion_assessment={
            "decision": "passed",
            "concepts": [{"id": "concept:one", "title": "完整概念一"}, {"id": "concept:two", "title": "完整概念二"}],
        },
    )
    outcome = SkillSynthesizer().synthesize(cluster, catalog)
    assert outcome.candidate is None
    assert any(item.code == "insufficient-semantic-signal" for item in outcome.validation.errors)


def test_actions_are_not_template_duplicates(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    assert result.skills
    for skill in result.skills:
        for index, left in enumerate(skill.actions):
            for right in skill.actions[index + 1:]:
                assert SequenceMatcher(None, left.text, right.text).ratio() < 0.85


def test_each_action_has_condition_action_object_output_and_stop(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    for skill in result.skills:
        assert ProceduralActionGate().validate(skill, catalog).passed
        for action in skill.actions:
            assert "原文信号“" in action.text
            assert any(item in action.text for item in ("输出", "生成", "记录"))
            assert any(item in action.text for item in ("停止", "拒绝", "升级"))


def test_semantic_action_is_supported_by_specific_evidence(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    for skill in result.skills:
        assessed = SemanticSupportGate().assess_skill(skill, catalog)
        source_hashes = {statement_hash(item) for item in skill.source_statements()}
        source_assessments = [
            item for item in assessed.assessments
            if item.statement_hash in source_hashes
        ]
        assert source_hashes
        assert len(source_assessments) == len(source_hashes)
        assert all(item.status == "supported" for item in source_assessments)
