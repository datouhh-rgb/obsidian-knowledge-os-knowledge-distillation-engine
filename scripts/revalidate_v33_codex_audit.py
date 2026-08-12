#!/usr/bin/env python3
"""Strictly revalidate retained real-Codex transcripts without rerunning Codex."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from knowledge_engine.utils import canonical_json, sha256_text


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def strict_skill_read(text: str, skill_name: str) -> bool:
    canonical = skill_name.casefold().replace("\\", "/")
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item", {})
        normalized = canonical_json(event).casefold().replace("\\\\", "/").replace("\\", "/")
        output = str(item.get("aggregated_output", "")).casefold()
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "command_execution"
            and item.get("status") == "completed"
            and item.get("exit_code") == 0
            and canonical in normalized
            and "skill.md" in normalized
            and ("invocation fingerprint" in output or "skill id:" in output)
        ):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.input.resolve()
    payload: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
    skill_name = str(payload["skill"]["name"])
    fingerprint = str(payload["skill"]["invocation_fingerprint"])

    artifact_failures: list[str] = []
    for item in payload.get("raw_evidence", []):
        path = ROOT / item["path"]
        if not path.is_file() or sha256(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            artifact_failures.append(str(item["path"]))

    sessions = payload["sessions"]
    observed: dict[str, bool] = {}
    for name, session in sessions.items():
        stdout = ROOT / session["raw_directory"] / "stdout.jsonl"
        observed[name] = stdout.is_file() and strict_skill_read(stdout.read_text(encoding="utf-8"), skill_name)
        session["observed_skill_read"] = observed[name]
        session["detection"] = "strict-content-read-event" if observed[name] else "no-successful-target-skill-content-read"

    discovery = sessions["discovery"]
    explicit = sessions["explicit"]
    discovery_pass = discovery["exit_code"] == 0 and skill_name in discovery["last_message"]
    explicit_pass = explicit["exit_code"] == 0 and observed["explicit"] and fingerprint in explicit["last_message"]

    raw_root = Path(sessions["discovery"]["raw_directory"]).parents[1]
    approval_root = ROOT / raw_root / "approval-eval"
    evaluations = list(approval_root.rglob("evaluation.json"))
    strict_eval: dict[str, Any] = {"passed": False, "reason": "evaluation artifact missing or ambiguous"}
    if len(evaluations) == 1:
        evaluation = json.loads(evaluations[0].read_text(encoding="utf-8"))
        cases: list[dict[str, Any]] = []
        for item in evaluation["cases"]:
            stdout = evaluations[0].parent / "cases" / item["case_uuid"] / "stdout.jsonl"
            current_observed = stdout.is_file() and strict_skill_read(stdout.read_text(encoding="utf-8"), skill_name)
            cases.append({**item, "observed": current_observed, "passed": current_observed == item["expected"] and item["exit_code"] == 0})
        positive = [item for item in cases if item["category"] == "positive"]
        negative = [item for item in cases if item["category"] == "negative"]
        boundary = [item for item in cases if item["category"] == "boundary"]
        metrics = {
            "trigger_recall": 100.0 * sum(item["observed"] for item in positive) / len(positive),
            "trigger_precision": 100.0 * sum(not item["observed"] for item in negative) / len(negative),
            "boundary_score": 100.0 * sum(item["passed"] for item in boundary) / len(boundary),
        }
        strict_eval = {
            "run_id": evaluation["run_id"], "case_count": len(cases),
            "counts": {category: sum(item["category"] == category for item in cases) for category in ("positive", "negative", "boundary")},
            "metrics": metrics,
            "passed": all(item["exit_code"] == 0 for item in cases) and all(value >= 80 for value in metrics.values()),
            "detector": "successful-command-output-containing-generated-skill-content",
        }

    checks = dict(payload.get("checks", {}))
    checks.update({
        "approval_real_codex_eval": bool(strict_eval.get("passed")),
        "approved_after_review": False,
        "human_review_recorded": True,
        "exposure_is_link_not_copy": payload["exposure"]["mechanism"] == "managed-directory-link" and payload["exposure"]["copied_skill_file"] is False,
        "codex_discovery": discovery_pass,
        "codex_explicit_call": explicit_pass,
        "trigger_positive": observed["trigger_positive"],
        "trigger_negative": not observed["trigger_negative"],
        "bad_description_block": not sessions["bad_description"].get("decoy_observed", False),
        "raw_evidence_integrity": not artifact_failures,
    })
    payload.update({
        "mode": "real-codex-cli-disposable-vault-strict-transcript-revalidation-no-mocks",
        "source_audit_path": source.relative_to(ROOT).as_posix(),
        "source_audit_sha256": sha256(source),
        "strict_revalidation": {
            "detector": "successful command output must contain generated Skill content",
            "approval_evaluation": strict_eval,
            "artifact_failures": artifact_failures,
            "discovery_response_sha256": sha256_text(discovery["last_message"]),
            "explicit_response_sha256": sha256_text(explicit["last_message"]),
        },
        "checks": checks,
        "status": "blocked",
        "blockers": sorted(name for name, passed in checks.items() if passed is not True),
    })
    payload["review"]["accepted_for_release"] = False
    payload["review"]["after_status"] = "invalidated-disposable-attempt"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"], "blockers": payload["blockers"],
        "strict_metrics": strict_eval.get("metrics"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
