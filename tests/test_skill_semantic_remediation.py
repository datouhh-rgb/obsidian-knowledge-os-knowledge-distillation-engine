from __future__ import annotations

import json
import re
from pathlib import Path
from zipfile import ZipFile

from knowledge_engine.evidence import EvidenceCatalog
from knowledge_engine.gates import ProceduralActionGate, SemanticSkillGate
from knowledge_engine.knowledge_synthesis import KnowledgeSynthesizer
from knowledge_engine.models import GroundedStatement, MethodCluster, SkillCandidate
from test_v33_knowledge_synthesis import stock_operator_synthesis_fixture


ROOT = Path(__file__).resolve().parents[1]


def _candidate(catalog: EvidenceCatalog, actions: tuple[GroundedStatement, ...]) -> SkillCandidate:
    policy = lambda text: GroundedStatement(text, (), "engine_policy")  # noqa: E731
    return SkillCandidate(
        title="风险信号核验方法",
        positioning=policy("使用风险信号核验方法检查市场信号。"),
        scenarios=(policy("当需要核验风险信号时使用。"),),
        input_requirements=(policy("提供风险信号与市场事实。"),),
        decision_flow=(policy("先列出风险信号。"), policy("再比较市场事实。")),
        actions=actions,
        output_format=policy("输出风险信号核验记录。"),
        boundaries=(policy("只处理绑定的风险 Evidence。"),),
        prohibited_scenarios=(policy("缺少风险 Evidence 时禁止使用。"),),
        provider="test",
        confidence=0.9,
    )


def test_raw_evidence_excerpt_cannot_be_skill_action(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, _ = stock_operator_synthesis_fixture
    evidence = next(iter(catalog.items.values()))
    candidate = _candidate(
        catalog,
        (
            GroundedStatement(evidence.excerpt, (evidence.evidence_id,), "source"),
            GroundedStatement(evidence.excerpt + "。", (evidence.evidence_id,), "source"),
        ),
    )
    report = ProceduralActionGate().validate(candidate, catalog)
    assert any(item.code == "procedural-action-raw-excerpt" for item in report.errors)


def test_embedded_raw_evidence_quote_cannot_be_action(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, _ = stock_operator_synthesis_fixture
    evidence = next(iter(catalog.items.values()))
    embedded = (
        f"当需要判断时，检查“{evidence.excerpt[:48]}”并记录结果；若无法确认则停止。"
    )
    report = ProceduralActionGate().validate(_candidate(
        catalog,
        (
            GroundedStatement(embedded, (evidence.evidence_id,), "source"),
            GroundedStatement(embedded + "并复核。", (evidence.evidence_id,), "source"),
        ),
    ), catalog)
    assert any(item.code == "procedural-action-raw-excerpt" for item in report.errors)


def test_narrative_quote_is_not_procedural_action(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, _ = stock_operator_synthesis_fixture
    evidence = next(iter(catalog.items.values()))
    candidate = _candidate(
        catalog,
        (
            GroundedStatement("我那位朋友拿不定主意到底做市场哪一边。", (evidence.evidence_id,), "source"),
            GroundedStatement("他把所有因素都过了一遍。", (evidence.evidence_id,), "source"),
        ),
    )
    report = ProceduralActionGate().validate(candidate, catalog)
    assert any(item.code == "procedural-action-narrative" for item in report.errors)


def test_action_requires_verb_object_condition_output(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, _ = stock_operator_synthesis_fixture
    evidence = next(iter(catalog.items.values()))
    good = GroundedStatement(
        "当风险扩大时，检查退出条件并记录结果；若无法确认则停止。",
        (evidence.evidence_id,), "source",
    )
    bad = GroundedStatement("风险扩大。", (evidence.evidence_id,), "source")
    assert ProceduralActionGate().validate(_candidate(catalog, (good, good)), catalog).passed
    codes = {item.code for item in ProceduralActionGate().validate(_candidate(catalog, (bad, bad)), catalog).errors}
    assert {"procedural-action-verb", "procedural-action-condition", "procedural-action-observable"}.issubset(codes)


def test_action_statement_has_valid_evidence_binding(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, _ = stock_operator_synthesis_fixture
    action = GroundedStatement("当风险扩大时，检查退出条件并记录结果；否则停止。", (), "engine_policy")
    report = ProceduralActionGate().validate(_candidate(catalog, (action, action)), catalog)
    assert any(item.code == "procedural-action-evidence-binding" for item in report.errors)


def test_generic_engine_policy_shell_is_blocked(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    cluster = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录").method_clusters[0]
    evidence = [catalog.get(item) for item in cluster.evidence_ids]
    actions = tuple(
        GroundedStatement(
            f"当需要执行时，检查“{item.excerpt[:20]}”并记录结果；无法确认则停止。",
            (item.evidence_id,), "source",
        )
        for item in evidence[:2] if item is not None
    )
    generic = _candidate(catalog, actions)
    generic = SkillCandidate(
        **{
            **generic.__dict__,
            "positioning": GroundedStatement("Apply Method Cluster purpose and boundaries.", (), "engine_policy"),
            "scenarios": (GroundedStatement(
                "Use when the task matches the Method Cluster purpose and the required source facts are available.",
                (), "engine_policy",
            ),),
        }
    )
    report = SemanticSkillGate().validate(cluster, generic, catalog)
    assert any(item.code in {"semantic-skill-generic-field", "semantic-skill-generic-shell"} for item in report.errors)


def test_exported_stock_operator_sample_is_rejected_before_fix() -> None:
    archive_path = ROOT / "history/V3.3_BLOCKED_Audit/SKILL_EXAMPLE_pre_remediation.zip"
    skill_entry = "apply-source-method-a4e20f8d-75dba888/versions/1.0.0/SKILL.md"
    evidence_entry = "apply-source-method-a4e20f8d-75dba888/versions/1.0.0/references/evidence.json"
    with ZipFile(archive_path) as archive:
        payload = json.loads(archive.read(evidence_entry).decode("utf-8"))
        text = archive.read(skill_entry).decode("utf-8")
    catalog = EvidenceCatalog.from_dict({
        "source_title": "股票大作手回忆录",
        "source_id": payload["source_id"],
        "conversion_id": payload["conversion_id"],
        "items": payload["evidence"],
    }, "")
    action_section = text.split("## 5. 执行动作", 1)[1].split("## 6.", 1)[0]
    actions = []
    for line in action_section.splitlines():
        match = re.match(r"\d+\.\s+(.+?)\s+· Evidence: `([^`]+)`", line.strip())
        if match:
            actions.append(GroundedStatement(match.group(1), (match.group(2),), "source"))
    report = ProceduralActionGate().validate(_candidate(catalog, tuple(actions)), catalog)
    assert len(actions) >= 2
    assert any(item.code == "procedural-action-raw-excerpt" for item in report.errors)


def test_synthesized_skill_is_executable_after_fix(stock_operator_synthesis_fixture) -> None:  # type: ignore[no-untyped-def]
    catalog, concepts = stock_operator_synthesis_fixture
    result = KnowledgeSynthesizer().synthesize(concepts, catalog, "股票大作手回忆录")
    assert result.skills
    clusters = {item.id: item for item in result.method_clusters}
    for candidate in result.skills:
        cluster = clusters[result.skill_method_ids[candidate.fingerprint()]]
        assert ProceduralActionGate().validate(candidate, catalog).passed
        assert SemanticSkillGate().validate(cluster, candidate, catalog).passed
        assert all(statement.evidence_ids for statement in candidate.actions)
