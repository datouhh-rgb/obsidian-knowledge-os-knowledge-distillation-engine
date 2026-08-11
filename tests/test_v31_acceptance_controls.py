from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from knowledge_engine.integrity import IntegrityReconciler
from knowledge_engine.lifecycle import SkillLifecycleService
from knowledge_engine.pipeline import DistillationPipeline
from knowledge_engine.providers import LLMAnalyzerProvider
from knowledge_engine.registry import REGISTRY_JSON, SkillRegistry
from knowledge_engine.synthesis import SynthesisRegistry
from knowledge_engine.transaction import (
    IdempotencyConflict,
    ManualRecoveryRequired,
    SimulatedCrash,
    VaultTransaction,
)
from knowledge_engine.trigger_eval import TriggerEvalError, TriggerEvalService, TriggerEvalValidator
from knowledge_engine.utils import atomic_write, sha256_file
from conftest import make_production_pipeline

from conftest import record_passing_eval


def test_trigger_cases_are_implicit_unique_and_expected_only(distilled) -> None:
    path = TriggerEvalService(distilled["vault"]).generate_cases(distilled["skill"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    TriggerEvalValidator.validate_case_set(payload)
    record = SkillRegistry.load(distilled["vault"]).require(distilled["skill"])
    prompts = [item["prompt"] for item in payload["cases"]]
    assert len(prompts) == len(set(prompts)) == 25
    assert all("observed" not in item and "passed" not in item for item in payload["cases"])
    assert all(record["canonical_name"].casefold() not in item.casefold() for item in prompts)
    assert all(record["title"].casefold() not in item.casefold() for item in prompts)
    assert all("$skill-name" not in item.casefold() for item in prompts)


def test_record_eval_rejects_boolean_only_and_missing_provenance(distilled) -> None:
    service = SkillLifecycleService(distilled["vault"])
    root = distilled["vault"] / "10_Reviews/Skill-Evals/forged"
    root.mkdir(parents=True)
    old = {"positive": [{"triggered": True}] * 10, "negative": [{"triggered": False}] * 10, "boundary": [{"correct": True}] * 5}
    path = root / "boolean-only.json"; path.write_text(json.dumps(old), encoding="utf-8")
    with pytest.raises((ValueError, TriggerEvalError), match="schema|real|boolean|kind"):
        service.record_eval(distilled["skill"], "1.0.0", old, path)


@pytest.mark.parametrize("executability", [50.0, 62.5, 75.0])
def test_low_executability_cannot_approve(distilled, executability: float) -> None:
    record_passing_eval(distilled["vault"], distilled["skill"])
    registry = SkillRegistry.load(distilled["vault"])
    _, _, lifecycle = registry.version(distilled["skill"], "1.0.0")
    lifecycle["quality"]["executability"] = executability
    lifecycle["quality"]["status"] = "pass"  # Numeric gate must still reject a forged status string.
    atomic_write(distilled["vault"] / REGISTRY_JSON, registry.to_json())
    with pytest.raises(ValueError, match="executability|quality"):
        SkillLifecycleService(distilled["vault"]).approve(distilled["skill"], "1.0.0", "human", "must fail")


def test_old_artifact_eval_cannot_be_recorded(distilled) -> None:
    payload, path, version = record_passing_eval(distilled["vault"], distilled["skill"])
    forged = dict(payload); forged["run_id"] = "old-artifact-run"; forged["artifact_sha256"] = "0" * 64
    forged_path = path.parent / "old-artifact.json"
    forged_path.write_text(json.dumps(forged, ensure_ascii=False, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="old|artifact"):
        SkillLifecycleService(distilled["vault"]).record_eval(distilled["skill"], version, forged, forged_path)


def test_semantic_review_is_immutable_event_not_assessment_edit(distilled) -> None:
    registry = SkillRegistry.load(distilled["vault"])
    _record, version_record, lifecycle = registry.version(distilled["skill"], "1.0.0")
    target = next(item for item in version_record["semantic_assessments"] if item["evidence_ids"])
    target["status"] = "partial"  # Acceptance fixture starts with a reviewable assessment.
    lifecycle["status"] = "review"
    atomic_write(distilled["vault"] / REGISTRY_JSON, registry.to_json())
    review = {
        "statement_hash": target["statement_hash"], "decision": "supported",
        "reason": "Human verified the exact bound excerpt.", "evidence_ids": target["evidence_ids"],
    }
    result = SkillLifecycleService(distilled["vault"]).semantic_review(
        distilled["skill"], "1.0.0", review, "pytest-reviewer",
    )
    assert result.event_id
    updated = SkillRegistry.load(distilled["vault"])
    _, immutable_version, updated_lifecycle = updated.version(distilled["skill"], "1.0.0")
    original = next(item for item in immutable_version["semantic_assessments"] if item["statement_hash"] == target["statement_hash"])
    effective = next(item for item in updated.effective_assessments(distilled["skill"], "1.0.0") if item["statement_hash"] == target["statement_hash"])
    assert original["status"] == "partial"
    assert effective["status"] == "supported"
    event_path = distilled["vault"] / updated_lifecycle["semantic_reviews"][-1]["event_path"]
    assert event_path.is_file()
    record_passing_eval(distilled["vault"], distilled["skill"])
    SkillLifecycleService(distilled["vault"]).approve(distilled["skill"], "1.0.0", "human", "review resolved")


def test_wal_recovers_after_bytes_replace_with_stale_marker(vault: Path) -> None:
    target = vault / "05_Knowledge/value.txt"
    target.write_text("before", encoding="utf-8")
    with pytest.raises(SimulatedCrash):
        with VaultTransaction(vault, "stale-marker", crash_after_replace=1) as tx:
            tx.stage_text("05_Knowledge/value.txt", "after")
            tx.commit()
    journals = list((vault / ".knowledge-engine/journal").glob("*.json"))
    assert journals
    journal = json.loads(journals[-1].read_text(encoding="utf-8"))
    assert journal["operations"][0]["operation_state"] == "replace_pending"
    assert target.read_text(encoding="utf-8") == "after"
    VaultTransaction.recover(vault)
    assert target.read_text(encoding="utf-8") == "before"
    assert json.loads(journals[-1].read_text(encoding="utf-8"))["status"] == "rolled_back"


def test_wal_neither_before_nor_after_requires_manual_recovery(vault: Path) -> None:
    target = vault / "05_Knowledge/value.txt"; target.write_text("before", encoding="utf-8")
    with pytest.raises(SimulatedCrash):
        with VaultTransaction(vault, "manual", crash_after_replace=1) as tx:
            tx.stage_text("05_Knowledge/value.txt", "after"); tx.commit()
    target.write_text("third-party", encoding="utf-8")
    with pytest.raises(ManualRecoveryRequired):
        VaultTransaction.recover(vault)
    journal = json.loads(next((vault / ".knowledge-engine/journal").glob("*.json")).read_text(encoding="utf-8"))
    assert journal["status"] == "manual_recovery_required"
    assert target.read_text(encoding="utf-8") == "third-party"


def test_idempotency_key_binds_operation_and_payload(vault: Path) -> None:
    with VaultTransaction(vault, "op-a", idempotency_key="key", request_payload={"value": 1}) as tx:
        tx.stage_text("05_Knowledge/idempotent.txt", "one"); first = tx.commit({"ok": 1})
    with VaultTransaction(vault, "op-a", idempotency_key="key", request_payload={"value": 1}) as tx:
        duplicate = tx.commit({"ok": 1})
    assert not first.duplicate and duplicate.duplicate
    with pytest.raises(IdempotencyConflict):
        with VaultTransaction(vault, "op-b", idempotency_key="key", request_payload={"value": 1}):
            pass
    with pytest.raises(IdempotencyConflict):
        with VaultTransaction(vault, "op-a", idempotency_key="key", request_payload={"value": 2}):
            pass


def test_cross_process_usage_events_are_consistent(distilled) -> None:
    vault = distilled["vault"]; skill = distilled["skill"]
    project_root = Path(__file__).parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project_root / "src")
    commands = [[
        sys.executable, "-m", "knowledge_engine", "record-use", skill,
        "--version", "1.0.0", "--project", "CrossProcess", "--outcome", "success",
        "--feedback", f"process-{index}", "--idempotency-key", f"process-{index}",
        "--vault", str(vault), "--json",
    ] for index in range(8)]

    def invoke(command: list[str]):
        return subprocess.run(command, cwd=project_root, env=environment, capture_output=True, text=True, timeout=90)

    with ThreadPoolExecutor(max_workers=8) as pool:
        completed = list(pool.map(invoke, commands))
    assert all(item.returncode == 0 for item in completed), [item.stderr for item in completed]
    registry = SkillRegistry.load(vault)
    _, _, lifecycle = registry.version(skill, "1.0.0")
    assert lifecycle["usage"]["invocation_count"] == 8
    assert len(registry.data["usage_events"]) == 8
    assert IntegrityReconciler(vault).reconcile().passed


@pytest.mark.parametrize("mutation", ["source_title", "source_text_sha256", "unknown_field"])
def test_asset_ledger_and_strict_schema_detect_metadata_tamper(distilled, mutation: str) -> None:
    conversion = next((distilled["vault"] / "06_Sources/Books").rglob("CONVERSION.json"))
    payload = json.loads(conversion.read_text(encoding="utf-8"))
    if mutation == "source_title": payload["source_title"] += " forged"
    elif mutation == "source_text_sha256": payload["source_text_sha256"] = "0" * 64
    else: payload["unknown_field"] = True
    conversion.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report = IntegrityReconciler(distilled["vault"]).reconcile()
    assert not report.passed
    assert any(not item["passed"] and item["code"] in {"conversion-load", "asset-ledger-byte-hashes"} for item in report.checks)


def test_different_titles_with_high_action_overlap_create_proposal(vault: Path, source_factory) -> None:
    steps = ["保存原始资料并计算哈希。", "记录原文行号和摘录。", "输出前核对结论边界。"]
    first = source_factory(book="A", method_title="来源核验流程", steps=steps)
    second = source_factory(book="B", method_title="原文证据检查方法", steps=steps)
    make_production_pipeline(vault).distill(str(first), title="A")
    result = make_production_pipeline(vault).distill(str(second), title="B")
    assert result.status == "completed"
    proposals = list(SynthesisRegistry.load(vault).proposals.values())
    matching = [item for item in proposals if {member["title"] for member in item["members"]} == {"来源核验流程", "原文证据检查方法"}]
    assert matching
    assert matching[0]["match"]["action_score"] >= 85


class CountingFakeLLM:
    name = "fake-longdoc"
    model = "fake-map-model"
    config = {"temperature": 0}

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def complete(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        evidence = request["evidence_selection_candidates"]
        if len(evidence) < 2:
            return {"concepts": [], "relations": [], "skills": []}
        first, second = evidence[0], evidence[1]
        statement = lambda item: {
            "text": item["excerpt"], "selected_evidence_indexes": [item["index"]],
        }
        return {
            "concepts": [{"title": f"证据核验 {first['section']}", "definition": statement(first), "confidence": 0.9}], "relations": [],
            "skills": [{
                "title": "长文证据核验流程", "positioning": statement(first),
                "scenarios": [statement(first)], "input_requirements": [statement(second)],
                "decision_flow": [statement(first), statement(second)],
                "actions": [statement(first), statement(second)], "output_format": statement(second),
                "boundaries": [statement(first)], "prohibited_scenarios": [statement(second)],
                "confidence": 0.9,
            }],
        }


def test_100k_llm_e2e_uses_true_map_reduce_and_rebinds_current_evidence(vault: Path, tmp_path: Path) -> None:
    def document(changed: bool = False) -> str:
        chapters = []
        for index in range(32):
            marker = "CHANGED" if changed and index == 17 else "stable"
            body = (f"chapter-{index} {marker} evidence sentence. " * 125)
            chapters.append(f"## Chapter {index}\n\n1. verify chapter {index} source.\n2. record chapter {index} locator.\n\n{body}")
        return "# Long Evidence Book\n\n" + "\n\n".join(chapters) + "\n"

    first_source = tmp_path / "long-one.md"; first_source.write_text(document(), encoding="utf-8")
    transport = CountingFakeLLM()
    first = DistillationPipeline(vault, provider=LLMAnalyzerProvider(transport), chunk_chars=7000).distill(
        str(first_source), title="Long Evidence Book", dry_run=True,
    )
    assert first.status == "dry-run" and len(document()) > 100_000
    assert len(transport.requests) == first.chunk_run["chunk_count"]
    assert max(len(json.dumps(item["evidence_selection_candidates"], ensure_ascii=False)) for item in transport.requests) < len(document()) // 4

    second_source = tmp_path / "long-two.md"; second_source.write_text(document(True), encoding="utf-8")
    second_transport = CountingFakeLLM()
    second = DistillationPipeline(vault, provider=LLMAnalyzerProvider(second_transport), chunk_chars=7000).distill(str(second_source), title="Long Evidence Book")
    run = second.chunk_run
    assert 0 < len(run["cache_hits"]) < run["chunk_count"]
    assert 0 < len(run["processed"]) < run["chunk_count"]
    assert len(run["cache_hits"]) + len(run["processed"]) == run["chunk_count"]
    registry = SkillRegistry.load(vault)
    current_records = [
        version for record in registry.records.values() for version in record["versions"].values()
        if any(source["conversion_id"] == second.conversion_id for source in version["sources"])
    ]
    assert second.status == "completed", {
        "warnings": second.warnings,
        "validation": [item.__dict__ for item in second.validation.issues],
    }
    assert current_records
    for version in current_records:
        assert all(evidence_id.startswith("ev-") for evidence_id in version["evidence_bindings"])
        evidence_path = vault / Path(version["artifact_path"]).parent / "references/evidence.json"
        evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert all(item["conversion_id"] == second.conversion_id for item in evidence_payload["evidence"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics run in Linux CI")
def test_posix_directory_symlink_unlinks_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target"; target.mkdir(); (target / "keep.txt").write_text("keep", encoding="utf-8")
    link = tmp_path / "link"; os.symlink(target, link, target_is_directory=True)
    VaultTransaction._remove_directory_link(link)
    assert not os.path.lexists(link)
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"
