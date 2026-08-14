#!/usr/bin/env python3
"""Strictly revalidate retained real-Codex transcripts without rerunning Codex."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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


def _normalized_path(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def revalidate_skills_list(
    stdout: str,
    raw_directory: Path,
    skill_name: str,
    description: str,
) -> dict[str, Any]:
    response: dict[str, Any] | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("id") == 25:
            response = event
            break
    data = (response or {}).get("result", {}).get("data", [])
    repo_name_entries: list[dict[str, Any]] = []
    canonical_matches: list[dict[str, Any]] = []
    errors: list[str] = []
    for scope in data if isinstance(data, list) else []:
        if not isinstance(scope, dict):
            continue
        if isinstance(scope.get("errors"), list):
            errors.extend(str(item) for item in scope["errors"])
        for item in scope.get("skills", []) if isinstance(scope.get("skills"), list) else []:
            if not isinstance(item, dict):
                continue
            path = _normalized_path(str(item.get("path", ""))).replace("\\", "/")
            expected_suffix = f"/.agents/skills/{skill_name}/skill.md".casefold()
            if item.get("name") != skill_name or item.get("scope") != "repo":
                continue
            repo_name_entries.append(item)
            if (
                item.get("description") == description
                and item.get("enabled") is True
                and path.casefold().endswith(expected_suffix)
                and "/versions/" not in path.casefold()
            ):
                canonical_matches.append(item)
    requests = raw_directory / "requests.jsonl"
    request_text = requests.read_text(encoding="utf-8", errors="replace") if requests.is_file() else ""
    force_reload = '"forceReload": true' in request_text or '"forceReload":true' in request_text
    return {
        "method": "skills/list",
        "force_reload": force_reload,
        "matching_entry_count": len(repo_name_entries),
        "canonical_entry_count": len(canonical_matches),
        "duplicate_name_count": max(0, len(repo_name_entries) - 1),
        "errors": errors,
        "passed": (
            len(repo_name_entries) == 1
            and len(canonical_matches) == 1
            and not errors
            and force_reload
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    default = ROOT / "release/evidence/codex_real_test/codex_real_integration.json"
    parser.add_argument("--input", type=Path, default=default)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.input.resolve()
    output = (args.output or args.input).resolve()
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
    discovery_raw = ROOT / discovery["raw_directory"]
    discovery_stdout = discovery_raw / "stdout.jsonl"
    catalog_revalidation = revalidate_skills_list(
        discovery_stdout.read_text(encoding="utf-8", errors="replace")
        if discovery_stdout.is_file() else "",
        discovery_raw,
        skill_name,
        str(payload["skill"]["frontmatter"]["description"]),
    )
    discovery_pass = discovery["exit_code"] == 0 and catalog_revalidation["passed"]
    explicit_pass = explicit["exit_code"] == 0 and observed["explicit"] and fingerprint in explicit["last_message"]

    raw_root = Path(sessions["discovery"]["raw_directory"]).parents[1]
    approval_root = ROOT / raw_root / "approval-eval"
    evaluations = list(approval_root.rglob("evaluation.json"))
    strict_eval: dict[str, Any] = {"passed": False, "reason": "evaluation artifact missing or ambiguous"}
    if len(evaluations) == 1:
        evaluation = json.loads(evaluations[0].read_text(encoding="utf-8"))
        cases: list[dict[str, Any]] = []
        for item in evaluation["cases"]:
            case_root = evaluations[0].parent / "cases" / item["case_uuid"]
            matching = [
                path for path in case_root.rglob("stdout.jsonl")
                if sha256(path) == item["transcript_sha256"]
            ]
            stdout = matching[0] if len(matching) == 1 else None
            current_observed = bool(
                stdout is not None
                and strict_skill_read(stdout.read_text(encoding="utf-8"), skill_name)
            )
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

    bad_stdout = ROOT / sessions["bad_description"]["raw_directory"] / "stdout.jsonl"
    decoy_observed = (
        bad_stdout.is_file()
        and strict_skill_read(bad_stdout.read_text(encoding="utf-8"), "bad-description-control")
    )
    sessions["bad_description"]["decoy_observed"] = decoy_observed

    checks = dict(payload.get("checks", {}))
    checks.update({
        "approval_real_codex_eval": bool(strict_eval.get("passed")),
        "codex_discovery": discovery_pass,
        "codex_explicit_call": explicit_pass,
        "trigger_positive": observed["trigger_positive"],
        "trigger_negative": not observed["negative"],
        "bad_description_block": not decoy_observed,
        "raw_evidence_integrity": not artifact_failures,
    })
    passed = all(value is True for value in checks.values())
    payload.update({
        "strict_revalidation": {
            "detector": "successful command output must contain generated Skill content",
            "approval_evaluation": strict_eval,
            "artifact_failures": artifact_failures,
            "discovery_response_sha256": sha256_text(discovery["last_message"]),
            "discovery_evaluation": catalog_revalidation,
            "explicit_response_sha256": sha256_text(explicit["last_message"]),
        },
        "checks": checks,
        "status": "passed" if passed else "blocked",
        "blockers": sorted(name for name, passed in checks.items() if passed is not True),
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"], "blockers": payload["blockers"],
        "strict_metrics": strict_eval.get("metrics"),
    }, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
