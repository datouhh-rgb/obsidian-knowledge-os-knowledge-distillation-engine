from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from knowledge_engine.pipeline import DistillationPipeline
from knowledge_engine.models import GroundedStatement, ProviderResult, SkillCandidate
from knowledge_engine.providers import RuleAnalyzerProvider
from knowledge_engine.registry import SkillRegistry


SOURCE_TEMPLATE = """# {book}

## 核心概念

证据链是从加工后的知识返回原始资料、哈希和具体位置的路径。

## 来源核验流程

1. 保存原始资料并计算 SHA-256。
2. 为每条知识记录原文行号和原文摘录。
3. 输出前检查结论是否超出原文支持范围。
4. 遇到数字、日期或表格时回看原始页面。

## 案例

例如，一份资料先保存原件，再把方法步骤写成卡片，并从卡片链接回原文。
"""


class FixtureEvidenceBoundProvider:
    """Deterministic test double for a constrained semantic production provider."""

    name = "llm:deterministic-fixture"

    def propose(self, analysis, catalog):  # type: ignore[no-untyped-def]
        diagnostic = RuleAnalyzerProvider().propose(analysis, catalog)
        result = ProviderResult(
            provider=self.name,
            concepts=diagnostic.concepts,
            provenance={"provider": self.name, "model": "pytest-fixture", "evidence_selection": "system-only"},
        )
        for method in analysis.methods:
            actions = tuple(
                GroundedStatement(step, catalog.ids_for_evidence(evidence))
                for step, evidence in zip(method.steps, method.evidence)
            )
            if len(actions) < 2 or any(not item.evidence_ids for item in actions):
                continue
            policy = lambda text: GroundedStatement(text, (), "engine_policy")
            result.skills.append(SkillCandidate(
                title=method.title,
                positioning=policy(f"Apply the evidence-bound {method.title} procedure."),
                scenarios=(policy(f"Use when the task explicitly requires {method.title}."),),
                input_requirements=(policy("Provide the target object, source facts, and constraints."),),
                decision_flow=(policy("Confirm the task matches the method."), policy("Verify every required input is present.")),
                actions=actions,
                output_format=policy("Return decisions, actions, open issues, and Evidence locators."),
                boundaries=(policy("Do not exceed what the selected Evidence supports."),),
                prohibited_scenarios=(policy("Do not run for unrelated tasks or missing critical inputs."),),
                provider=self.name,
                confidence=0.9,
            ))
        return result


def make_production_pipeline(vault: Path, **kwargs):  # type: ignore[no-untyped-def]
    return DistillationPipeline(vault, provider=FixtureEvidenceBoundProvider(), **kwargs)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "05_Knowledge").mkdir(parents=True)
    (root / "06_Sources").mkdir(parents=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    return root


@pytest.fixture
def source_factory(tmp_path: Path):
    counter = 0

    def create(book: str = "可追溯学习手册", suffix: str = "", method_title: str = "来源核验流程", steps: list[str] | None = None) -> Path:
        nonlocal counter
        counter += 1
        text = SOURCE_TEMPLATE.format(book=book).replace("来源核验流程", method_title)
        if steps is not None:
            start = text.index(f"## {method_title}")
            end = text.index("\n## 案例", start)
            text = text[:start] + f"## {method_title}\n\n" + "\n".join(f"{i + 1}. {step}" for i, step in enumerate(steps)) + "\n" + text[end:]
        if suffix:
            text += f"\n## 附录\n\n{suffix}\n"
        path = tmp_path / f"source-{counter}.md"
        path.write_text(text, encoding="utf-8")
        return path

    return create


@pytest.fixture
def distilled(vault: Path, source_factory):
    source = source_factory()
    result = make_production_pipeline(vault).distill(str(source), title="可追溯学习手册")
    registry = SkillRegistry.load(vault)
    record = next(iter(registry.records.values()))
    return {"vault": vault, "source": source, "result": result, "skill": record["canonical_name"], "record": record}


def record_passing_eval(vault: Path, skill_name: str):
    from knowledge_engine.lifecycle import SkillLifecycleService
    from knowledge_engine.trigger_eval import TriggerEvalService
    from knowledge_engine.utils import sha256_bytes, sha256_text

    registry = SkillRegistry.load(vault)
    record, version_record, _ = registry.version(skill_name)
    version = record["current_version"]
    cases_path = TriggerEvalService(vault).generate_cases(skill_name, version)
    case_set = json.loads(cases_path.read_text(encoding="utf-8"))
    run_id = "fixture-approved-external-router"
    root = Path("10_Reviews/Skill-Evals") / skill_name / version / run_id
    evaluator = {
        "kind": "approved-external-router", "cli_version": "pytest-fixture-1",
        "model": "deterministic-fixture", "command_sha256": sha256_text("pytest-router"),
    }
    cases = []
    for case in case_set["cases"]:
        case_root = root / "cases" / case["case_uuid"]
        transcript = (json.dumps({"type": "thread.started", "thread_id": case["case_uuid"]}) + "\n").encode()
        stderr = b""; last = b""
        paths = {
            "transcript_path": case_root / "stdout.jsonl",
            "stderr_path": case_root / "stderr.txt",
            "last_message_path": case_root / "last-message.txt",
        }
        for key, relative in paths.items():
            target = vault / relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(transcript if key == "transcript_path" else b"")
        cases.append({
            **case, "observed": case["expected"], "passed": True, "exit_code": 0,
            "command_sha256": evaluator["command_sha256"], "evaluator": evaluator,
            "transcript_path": paths["transcript_path"].as_posix(), "transcript_sha256": sha256_bytes(transcript),
            "stderr_path": paths["stderr_path"].as_posix(), "stderr_sha256": sha256_bytes(stderr),
            "last_message_path": paths["last_message_path"].as_posix(), "last_message_sha256": sha256_bytes(last),
            "detection": "approved-test-router-attestation",
        })
    payload = {
        "schema_version": 1, "kind": "real-trigger-eval", "run_id": run_id,
        "skill_id": record["skill_id"], "skill": skill_name, "version": version,
        "artifact_sha256": version_record["artifact_sha256"],
        "case_set_sha256": case_set["case_set_sha256"], "created_at": "2026-01-01T00:00:00+00:00",
        "evaluator": evaluator, "cases": cases,
        "metrics": {"trigger_recall": 100.0, "trigger_precision": 100.0, "boundary_score": 100.0},
        "false_positives": [], "false_negatives": [], "passed": True,
    }
    evaluation_path = vault / root / "evaluation.json"
    evaluation_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lifecycle = SkillLifecycleService(vault)
    lifecycle.record_eval(skill_name, version, payload, evaluation_path)
    return payload, evaluation_path, version


def approve_skill(vault: Path, skill_name: str) -> None:
    from knowledge_engine.lifecycle import SkillLifecycleService

    _payload, _evaluation_path, version = record_passing_eval(vault, skill_name)
    lifecycle = SkillLifecycleService(vault)
    lifecycle.approve(skill_name, version, "pytest-human", "explicit test approval")
