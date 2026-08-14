from __future__ import annotations

from pathlib import Path

from knowledge_engine.concepts import BookKnowledgeGate, ConceptQualityGate
from knowledge_engine.models import ConceptCandidate, GroundedStatement, ProviderResult
from knowledge_engine.pipeline import DistillationPipeline


def _concept(title: str, evidence: str = "ev-1", definition: str | None = None) -> ConceptCandidate:
    return ConceptCandidate(
        title,
        GroundedStatement(definition or f"{title}是一个具有完整语义的判断原则。", (evidence,) if evidence else (), "source"),
        "llm:test",
        0.9,
    )


def test_single_bad_concept_is_quarantined_not_book_blocker() -> None:
    result = ConceptQualityGate().validate([_concept("市场反馈"), _concept("则")])
    assessment = BookKnowledgeGate().validate(result)
    assert {item.title for item in result.accepted} == {"市场反馈"}
    assert {item.title for item in result.quarantined} == {"则"}
    assert assessment.passed
    assert assessment.quarantined_count == 1


def test_candidate_quarantine_does_not_automatically_abort_clean_book() -> None:
    result = ConceptQualityGate().validate([_concept("注意力选择"), _concept("则")])
    assessment = BookKnowledgeGate().validate(result)
    assert assessment.passed
    assert {item.title for item in result.accepted} == {"注意力选择"}
    assert {item.title for item in result.quarantined} == {"则"}


def test_quarantined_candidate_never_reaches_graph_or_skill(tmp_path: Path, vault: Path) -> None:
    class Provider:
        name = "llm:candidate-isolation"

        def propose(self, analysis, catalog):  # type: ignore[no-untyped-def]
            evidence = list(catalog.items.values())
            concepts = [
                _concept("市场反馈", evidence[0].evidence_id, evidence[0].excerpt),
                _concept("趋势信号", evidence[1].evidence_id, evidence[1].excerpt),
                _concept("风险条件", evidence[2].evidence_id, evidence[2].excerpt),
                _concept("则", evidence[0].evidence_id, "则表示一个不完整碎片。"),
            ]
            return ProviderResult(provider=self.name, concepts=concepts)

    source = tmp_path / "candidate-isolation.md"
    source.write_text(
        "# 一\n\n观察市场反馈并记录事实。\n\n识别趋势信号并比较连续变化。\n\n检查风险条件并记录结果。\n",
        encoding="utf-8",
    )
    result = DistillationPipeline(vault, provider=Provider(), chunk_chars=30_000).distill(str(source), title="候选隔离")
    assert result.status == "completed"
    assert result.concept_gate["quarantined_count"] == 1
    assert result.concept_gate["passed"] is True
    graph_text = (vault / "05_Knowledge/Graph/concept-graph.v3.json").read_text(encoding="utf-8")
    assert '"title": "则"' not in graph_text
    assert all("则" not in path.read_text(encoding="utf-8") for path in result.skills)


def test_systemic_fragment_rate_blocks_book() -> None:
    candidates = [_concept("有效概念")] + [_concept("则") for _ in range(10)]
    result = ConceptQualityGate().validate(candidates)
    assessment = BookKnowledgeGate().validate(result)
    assert not assessment.passed
    assert "invalid rate" in assessment.block_reason


def test_systematic_fragment_rate_blocks_book() -> None:
    candidates = [_concept("有效概念")] + [_concept("则") for _ in range(10)]
    assessment = BookKnowledgeGate().validate(ConceptQualityGate().validate(candidates))
    assert not assessment.passed
    assert "invalid rate" in assessment.block_reason


def test_zero_viable_method_blocks_book() -> None:
    assessment = BookKnowledgeGate().validate(ConceptQualityGate().validate([_concept("则")]))
    assert not assessment.passed
    assert "zero viable knowledge" in assessment.block_reason


def test_cognitive_awakening_candidate_title_regression() -> None:
    result = ConceptQualityGate().validate([
        _concept("注意力选择", definition="注意力选择决定了可被感知和处理的信息。"),
        _concept("则", definition="则"),
        _concept("这就", definition="这就是一个不完整的过渡语。"),
    ])
    assessment = BookKnowledgeGate().validate(result)
    assert assessment.passed
    assert {item.title for item in result.quarantined} == {"则", "这就"}
    assert assessment.block_reason == ""


def test_narrative_metadata_and_decoder_fragments_are_quarantined() -> None:
    result = ConceptQualityGate().validate([
        _concept("市场趋势判断", definition="市场趋势判断需要观察可核验的价格信号。"),
        _concept("我从来都", definition="我从来都是单打独斗。"),
        _concept("集团社址 南京市湖南路1号A楼，邮编", definition="出版社联系信息。"),
        _concept("Market trend�alignment", definition="A decoder-damaged candidate."),
    ])
    assert {item.title for item in result.accepted} == {"市场趋势判断"}
    assert {item.title for item in result.quarantined} == {
        "我从来都", "集团社址 南京市湖南路1号A楼，邮编", "Market trend�alignment",
    }


def test_short_complete_concept_does_not_override_hard_fragment_reason() -> None:
    result = ConceptQualityGate().validate([
        _concept("熵", definition="熵用于描述系统无序程度。"),
        _concept("我不", definition="我不认为这是完整的概念标签。"),
    ])
    assert {item.title for item in result.accepted} == {"熵"}
    assert {item.title for item in result.quarantined} == {"我不"}
