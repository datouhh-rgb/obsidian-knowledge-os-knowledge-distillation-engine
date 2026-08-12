#!/usr/bin/env python3
"""Render V3.3 release Markdown from canonical machine evidence only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def passed(payload: dict[str, Any]) -> bool:
    checks = payload.get("checks", payload.get("release_checks", {}))
    return payload.get("status") == "passed" and isinstance(checks, dict) and all(checks.values())


def consistent_release_commit(*payloads: dict[str, Any]) -> tuple[bool, str]:
    commits = {str(payload.get("commit_sha", "")) for payload in payloads}
    valid = len(commits) == 1 and "" not in commits and "unknown" not in commits
    return valid, next(iter(commits), "") if valid else ""


def write(relative: str, lines: list[str]) -> None:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ci", type=Path, default=ROOT / "release/evidence/ci/cross_platform_matrix.json")
    parser.add_argument("--codex", type=Path, default=ROOT / "release/evidence/codex_real_test/codex_real_integration.json")
    parser.add_argument("--stock", type=Path, default=ROOT / "release/evidence/regressions/stock_operator_v33_canonical.json")
    parser.add_argument("--cognitive", type=Path, default=ROOT / "release/evidence/regressions/cognitive_awakening_v33_canonical.json")
    args = parser.parse_args()
    ci, codex, stock, cognitive = map(load, (args.ci, args.codex, args.stock, args.cognitive))
    same_commit, commit = consistent_release_commit(ci, codex, stock, cognitive)
    all_pass = ci.get("status") == "passed" and passed(codex) and passed(stock) and passed(cognitive) and same_commit
    status = "V3.3.0_PRODUCTION_READY" if all_pass else "V3.3.0_BLOCKED"

    write("release/REAL_CODEX_EXPLICIT_ONLY_REPORT.md", [
        "# Real Codex Explicit-Only Report", "",
        f"- Status: `{'PASS' if passed(codex) else 'BLOCKED'}`",
        f"- Commit: `{codex.get('commit_sha')}`",
        f"- CLI: `{codex.get('codex_cli_version')}`",
        f"- Run ID: `{codex.get('run_id')}`",
        f"- Profile: `{codex.get('profile')}`",
        f"- Skill: `{codex.get('skill', {}).get('name')}`",
        f"- Deployment: `{codex.get('exposure', {}).get('deployment_mode')}`",
        f"- Discovery: `{codex.get('checks', {}).get('codex_discovery')}`",
        f"- Explicit call: `{codex.get('checks', {}).get('codex_explicit_call')}`",
        f"- Exact SKILL.md read: `{codex.get('sessions', {}).get('explicit', {}).get('observed_skill_read')}`",
        "- Implicit invocation: disabled; no implicit precision/recall is claimed.",
    ])
    write("release/BOOK_REGRESSION_REPORT.md", [
        "# V3.3.0 Canonical Book Regression Report", "",
        f"- Commit identity consistent: `{same_commit}`", "",
        "| Book | Status | Run | Chunks | Methods | Skills |",
        "|---|---|---|---:|---:|---:|",
        f"| 股票大作手回忆录 | {stock.get('status')} | `{stock.get('run_id')}` | {stock.get('chunk_count')} | {stock.get('method_cluster_count')} | {stock.get('skill_candidate_count')} |",
        f"| 认知觉醒 | {cognitive.get('status')} | `{cognitive.get('run_id')}` | {cognitive.get('chunk_count')} | {cognitive.get('method_cluster_count')} | {cognitive.get('skill_candidate_count')} |",
        "", "Both runs use disposable Vaults; production Vault access is prohibited.",
    ])
    stock_skills = stock.get("knowledge_synthesis", {}).get("skills", [])
    write("release/SKILL_SEMANTIC_QUALITY_REPORT.md", [
        "# Skill Semantic Quality Report", "",
        f"- Stock candidate count: `{len(stock_skills)}`",
        f"- Raw Evidence action gate: `{'PASS' if stock.get('release_checks', {}).get('skill_actions_not_raw_evidence') else 'BLOCKED'}`",
        f"- Method cohesion gate: `{'PASS' if stock.get('release_checks', {}).get('method_clusters_cohesive') else 'BLOCKED'}`",
        "- Each accepted action must carry a verb, object, condition, observable output/stop condition, and statement-level Evidence binding.",
        "- Generic engine-policy shells and narrative quotations are quarantined.",
    ])
    final_lines = [
        "# V3.3.0 Final Production Acceptance Report", "",
        f"- Final status: `{status}`", f"- Commit SHA: `{commit}`",
        f"- GitHub Actions: {ci.get('workflow_url', '')}",
        f"- Portable matrix: `{ci.get('status')}` (jobs: {len(ci.get('jobs', []))}/6)",
        f"- Real Codex explicit-only: `{'PASS' if passed(codex) else 'BLOCKED'}`",
        f"- 股票大作手回忆录: `{stock.get('status')}`",
        f"- 认知觉醒: `{cognitive.get('status')}`",
        f"- One release commit: `{same_commit}`", "",
        "## Known limitations", "",
        "- Implicit invocation remains disabled and is not a release claim.",
        "- No production project connection is created.",
        "- Windows uses a true directory symlink when permitted, otherwise a hash-verified read-only managed deployment cache.",
    ]
    write("release/FINAL_ACCEPTANCE_REPORT.md", final_lines)
    write("FINAL_PRODUCTION_ACCEPTANCE_REPORT.md", final_lines)
    print(status)
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
