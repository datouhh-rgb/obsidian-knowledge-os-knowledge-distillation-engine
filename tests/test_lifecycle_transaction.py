from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from knowledge_engine.graph import ConceptGraph
from knowledge_engine.integrity import IntegrityReconciler
from knowledge_engine.lifecycle import SkillLifecycleService
from knowledge_engine.registry import REGISTRY_JSON, SkillRegistry
from knowledge_engine.transaction import SimulatedCrash
from knowledge_engine.utils import sha256_file

from conftest import approve_skill


def test_success_does_not_activate_and_usage_is_version_bound(distilled) -> None:
    service = SkillLifecycleService(distilled["vault"])
    service.record_use(distilled["skill"], "AlphaFactory", "success", "worked", version="1.0.0")
    record, _, lifecycle = SkillRegistry.load(distilled["vault"]).version(distilled["skill"], "1.0.0")
    assert lifecycle["status"] == "candidate"
    assert lifecycle["usage"]["invocation_count"] == 1
    policy = (distilled["vault"] / "05_Knowledge/Skills" / distilled["skill"] / "agents/openai.yaml").read_text(encoding="utf-8")
    assert "allow_implicit_invocation: false" in policy
    event = next(iter(SkillRegistry.load(distilled["vault"]).data["usage_events"].values()))
    assert event["version"] == "1.0.0"


def test_twenty_concurrent_usage_events_are_consistent(distilled) -> None:
    vault = distilled["vault"]
    skill = distilled["skill"]

    def run(index: int) -> None:
        SkillLifecycleService(vault).record_use(
            skill, "AlphaFactory", "success", f"feedback-{index}", version="1.0.0",
            idempotency_key=f"concurrent-{index}",
        )

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(run, range(20)))
    registry = SkillRegistry.load(vault)
    _, _, lifecycle = registry.version(skill, "1.0.0")
    usage = lifecycle["usage"]
    assert usage["invocation_count"] == 20
    assert usage["outcomes"]["success"] == 20
    assert len(usage["feedback"]) == 20
    assert len(registry.data["usage_events"]) == 20
    assert len(list((vault / "10_Reviews/Skill-Usage" / skill / "1.0.0").glob("*.md"))) == 20
    graph = ConceptGraph.load(vault)
    assert len([edge for edge in graph.edges.values() if edge["type"] == "used_in_project"]) == 20
    assert IntegrityReconciler(vault).reconcile().passed


def test_same_idempotency_key_counts_once(distilled) -> None:
    vault = distilled["vault"]
    skill = distilled["skill"]

    def run(_: int) -> None:
        SkillLifecycleService(vault).record_use(skill, "P", "partial", "same", version="1.0.0", idempotency_key="same-key")

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(run, range(20)))
    registry = SkillRegistry.load(vault)
    _, _, lifecycle = registry.version(skill, "1.0.0")
    assert lifecycle["usage"]["invocation_count"] == 1
    assert len(registry.data["usage_events"]) == 1


@pytest.mark.parametrize("fail_after", range(0, 7))
def test_fault_injection_rolls_back_all_core_writes(distilled, fail_after: int) -> None:
    vault = distilled["vault"]
    before = sha256_file(vault / REGISTRY_JSON)
    with pytest.raises(OSError):
        SkillLifecycleService(vault).record_use(
            distilled["skill"], "FaultProject", "failed", "fault", version="1.0.0",
            idempotency_key=f"fault-{fail_after}", fail_after=fail_after,
        )
    assert sha256_file(vault / REGISTRY_JSON) == before
    assert not list((vault / "10_Reviews/Skill-Usage" / distilled["skill"] / "1.0.0").glob("*.md"))


def test_crash_wal_recovery_restores_consistent_state(distilled) -> None:
    vault = distilled["vault"]
    before = sha256_file(vault / REGISTRY_JSON)
    with pytest.raises(SimulatedCrash):
        SkillLifecycleService(vault).record_use(
            distilled["skill"], "CrashProject", "failed", "crash", version="1.0.0",
            idempotency_key="crash-key", crash_after=3,
        )
    from knowledge_engine.integrity import IntegrityReconciler

    report = IntegrityReconciler(vault).reconcile(repair=True)
    assert report.recovered_transactions
    assert sha256_file(vault / REGISTRY_JSON) == before
    assert report.passed


def test_eval_approve_then_exposure_eligible(distilled) -> None:
    approve_skill(distilled["vault"], distilled["skill"])
    _, _, lifecycle = SkillRegistry.load(distilled["vault"]).version(distilled["skill"], "1.0.0")
    assert lifecycle["status"] == "approved"
    assert lifecycle["evals"][-1]["passed"] is True


def test_approval_requires_eval(distilled) -> None:
    with pytest.raises(ValueError, match="eval"):
        SkillLifecycleService(distilled["vault"]).approve(distilled["skill"], "1.0.0", "human", "no eval")
