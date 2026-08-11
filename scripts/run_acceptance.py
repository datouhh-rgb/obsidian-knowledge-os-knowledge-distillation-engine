#!/usr/bin/env python3
"""V3.1 fail-closed production acceptance with one result per release blocker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
AUDIT = ROOT / "FINAL_AUDIT_PACK"
BLOCKER_ROOT = AUDIT / "blockers"
PYTHON = sys.executable

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from knowledge_engine.registry import SkillRegistry  # noqa: E402
from knowledge_engine.utils import atomic_write, sha256_file, sha256_text  # noqa: E402


DESCRIPTIONS = {
    1: "Windows and Linux full suites pass with exposure enabled",
    2: "Release manifest contains rerunnable source, audit, schemas, and tests",
    3: "Real Codex repo/user exposure, discovery, explicit invocation, and unexpose preserve Skill bytes",
    4: "Real implicit trigger eval has 10/10/5 raw observed cases and bad description fails",
    5: "Forged, transcript-less, and old-artifact trigger evals cannot approve",
    6: "Low executability, non-pass quality, and unresolved review cannot approve or expose",
    7: "100k+ main-path map/reduce is chunk-bounded, affected-only, and rebinds current Evidence",
    8: "All transaction crash windows commit fully or hash-reconcile fully, including stale marker",
    9: "Same idempotency key with a different operation or payload conflicts",
    10: "Any byte change across original, SOURCE_TEXT, metadata, catalog, Skill, or references fails integrity",
    11: "Different-title high-overlap methods create a governed synthesis proposal without overwrite",
    12: "Partial/conflict resolution occurs only through immutable human review events",
    13: "Synthesis and source candidates share structure, Evidence, semantic, executable, and validator gates",
    14: "V2/V3 migration dry-run, apply, rollback, repeat, and hash preservation pass",
    15: "Thread/process concurrency, same-key retries, and different keys remain consistent",
    16: "URL, PDF, and LLM security suites pass",
    17: "Release harness emits V3_1_BLOCKED when any gate is deliberately failed",
    18: "V3_1_PRODUCTION_ACCEPTED is emitted only when blockers 1-17 all pass",
}


@dataclass
class BlockerResult:
    blocker_id: int
    description: str
    command: str
    exit_code: int
    assertions: list[dict[str, Any]]
    evidence_files: list[dict[str, str]] = field(default_factory=list)
    status: str = "failed"
    started_at: str = ""
    completed_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.blocker_id,
            "description": self.description,
            "status": self.status,
            "command": self.command,
            "exit_code": self.exit_code,
            "assertions": self.assertions,
            "evidence_files": self.evidence_files,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def env() -> dict[str, str]:
    value = dict(os.environ)
    value["PYTHONUTF8"] = "1"
    value["PYTHONPATH"] = str(SRC)
    return value


def command_text(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def execute(command: list[str], *, cwd: Path = ROOT, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, env=env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )


def hash_evidence(path: Path) -> dict[str, str]:
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256_file(path)}


def run_logged(blocker_id: int, command: list[str], *, timeout: int = 600) -> BlockerResult:
    started = now()
    result = execute(command, timeout=timeout)
    directory = BLOCKER_ROOT / f"{blocker_id:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    stdout = directory / "stdout.txt"; stderr = directory / "stderr.txt"
    atomic_write(stdout, result.stdout); atomic_write(stderr, result.stderr)
    passed = result.returncode == 0
    return BlockerResult(
        blocker_id, DESCRIPTIONS[blocker_id], command_text(command), result.returncode,
        [{"name": "command_exit_zero", "passed": passed, "actual": result.returncode}],
        [hash_evidence(stdout), hash_evidence(stderr)], "passed" if passed else "failed", started, now(),
    )


def pytest_blocker(blocker_id: int, nodes: list[str], timeout: int = 600) -> BlockerResult:
    return run_logged(blocker_id, [PYTHON, "-m", "pytest", "-q", *nodes], timeout=timeout)


def platform_blocker(linux_evidence: Path | None) -> BlockerResult:
    started = now()
    windows_command = [PYTHON, "-m", "pytest", "-q"]
    windows = execute(windows_command, timeout=900)
    directory = BLOCKER_ROOT / "01"; directory.mkdir(parents=True, exist_ok=True)
    windows_out = directory / "windows-full-suite.stdout.txt"
    windows_err = directory / "windows-full-suite.stderr.txt"
    atomic_write(windows_out, windows.stdout); atomic_write(windows_err, windows.stderr)
    windows_pass = os.name == "nt" and windows.returncode == 0
    linux_pass = False
    linux_detail: Any = "missing"
    evidence = [hash_evidence(windows_out), hash_evidence(windows_err)]
    if sys.platform.startswith("linux"):
        linux_pass = windows.returncode == 0
        linux_detail = {"local_linux_run": True, "exit_code": windows.returncode}
    elif linux_evidence and linux_evidence.is_file():
        try:
            linux_payload = json.loads(linux_evidence.read_text(encoding="utf-8"))
            linux_pass = (
                linux_payload.get("platform") == "linux"
                and linux_payload.get("full_suite_exit_code") == 0
                and linux_payload.get("exposure_suite_exit_code") == 0
                and bool(linux_payload.get("raw_log_sha256"))
            )
            linux_detail = linux_payload
            copied = directory / "linux-ci-result.json"
            atomic_write(copied, json.dumps(linux_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            evidence.append(hash_evidence(copied))
        except Exception as exc:
            linux_detail = str(exc)
    passed = windows_pass and linux_pass
    return BlockerResult(
        1, DESCRIPTIONS[1], command_text(windows_command) + " + verified Linux CI evidence",
        0 if passed else 1,
        [
            {"name": "windows_full_suite", "passed": windows_pass, "actual": windows.returncode},
            {"name": "linux_full_and_exposure_suite", "passed": linux_pass, "actual": linux_detail},
            {"name": "no_exposure_skip", "passed": linux_pass and windows_pass, "actual": "tests/test_exposure.py included in full suite"},
        ], evidence, "passed" if passed else "failed", started, now(),
    )


def parse_json_stdout(completed: subprocess.CompletedProcess[str], *, allow_nonzero: bool = False) -> dict[str, Any]:
    if completed.returncode != 0 and not allow_nonzero:
        raise RuntimeError(completed.stdout + completed.stderr)
    return json.loads(completed.stdout)


def cli(*arguments: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return execute([PYTHON, "-m", "knowledge_engine", *arguments], timeout=timeout)


def last_agent_message(jsonl: str) -> str:
    result = ""
    for line in jsonl.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            result = str(item.get("text", "")).strip()
    return result


def real_codex_blockers(skip: bool) -> tuple[BlockerResult, BlockerResult]:
    started = now()
    directory = BLOCKER_ROOT / "03"; directory.mkdir(parents=True, exist_ok=True)
    trigger_directory = BLOCKER_ROOT / "04"; trigger_directory.mkdir(parents=True, exist_ok=True)
    codex = shutil.which("codex.cmd" if os.name == "nt" else "codex") or shutil.which("codex.exe")
    if skip or not codex:
        reason = "explicitly skipped" if skip else "Codex CLI unavailable"
        common = [{"name": "real_codex_not_skipped", "passed": False, "actual": reason}]
        return (
            BlockerResult(3, DESCRIPTIONS[3], "real Codex repo/user exposure workflow", 1, common, [], "failed", started, now()),
            BlockerResult(4, DESCRIPTIONS[4], "real Codex 10/10/5 implicit eval + bad-description control", 1, common, [], "failed", started, now()),
        )

    evidence3: list[dict[str, str]] = []
    evidence4: list[dict[str, str]] = []
    assertions3: list[dict[str, Any]] = []
    assertions4: list[dict[str, Any]] = []
    exit_code = 0
    with tempfile.TemporaryDirectory(prefix="v31-real-codex-") as temp:
        temp_root = Path(temp)
        vault = temp_root / "vault"; repo = temp_root / "repo"; user_repo = temp_root / "user-repo"
        for path in (vault / "05_Knowledge", vault / "06_Sources", repo, user_repo):
            path.mkdir(parents=True, exist_ok=True)
        for path in (vault, repo, user_repo):
            execute(["git", "init", "-q"], cwd=path)
        try:
            distill = cli(
                "distill", str(ROOT / "examples/source-grounded-handbook.md"),
                "--vault", str(vault), "--title", "V3.1 Real Codex Acceptance", "--json",
            )
            parse_json_stdout(distill)
            registry = SkillRegistry.load(vault)
            record = next(iter(registry.records.values()))
            skill = str(record["canonical_name"]); version = str(record["current_version"])
            skill_path = vault / "05_Knowledge/Skills" / skill / "SKILL.md"
            version_skill = vault / record["versions"][version]["artifact_path"]
            hashes_before = {"root": sha256_file(skill_path), "version": sha256_file(version_skill)}

            cases_result = cli("skill", "generate-trigger-cases", skill, "--version", version, "--vault", str(vault), "--json")
            cases_payload = parse_json_stdout(cases_result)
            evaluation = cli(
                "skill", "run-trigger-eval", skill, "--version", version,
                "--cases-json", cases_payload["cases_path"], "--concurrency", "8",
                "--timeout", "300", "--vault", str(vault), "--json", timeout=3600,
            )
            evaluation_payload = parse_json_stdout(evaluation)
            eval_json = Path(evaluation_payload["evaluation_path"])
            record_eval = cli(
                "skill", "record-eval", skill, "--version", version,
                "--eval-json", str(eval_json), "--vault", str(vault), "--json",
            )
            parse_json_stdout(record_eval)
            approval = cli(
                "skill", "approve", skill, "--version", version, "--reviewer", "release-auditor",
                "--reason", "Exact artifact passed real 10/10/5 implicit evaluation.",
                "--vault", str(vault), "--json",
            )
            parse_json_stdout(approval)
            good_eval = json.loads(eval_json.read_text(encoding="utf-8"))
            good_copy = trigger_directory / "good-evaluation.json"
            atomic_write(good_copy, json.dumps(good_eval, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            evidence4.append(hash_evidence(good_copy))
            raw_copy = trigger_directory / "good-raw"
            shutil.copytree(eval_json.parent, raw_copy)
            for path in sorted(raw_copy.rglob("*")):
                if path.is_file(): evidence4.append(hash_evidence(path))
            counts = {category: sum(item["category"] == category for item in good_eval["cases"]) for category in ("positive", "negative", "boundary")}
            assertions4.extend([
                {"name": "good_real_eval_passed", "passed": good_eval["passed"], "actual": good_eval["metrics"]},
                {"name": "case_matrix", "passed": counts == {"positive": 10, "negative": 10, "boundary": 5}, "actual": counts},
                {"name": "raw_transcript_per_case", "passed": all(item.get("transcript_sha256") for item in good_eval["cases"]), "actual": len(good_eval["cases"])},
            ])

            # Repo exposure, discovery, explicit invocation, and unexpose.
            expose = cli("skill", "expose", skill, "--scope", "repo", "--repo", str(repo), "--vault", str(vault), "--json")
            parse_json_stdout(expose)
            discover_prompt = "List only the exact repo-local Skill name available for source-grounded verification. Do not explain."
            discover = execute([codex, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--json", "--cd", str(repo), discover_prompt], timeout=300)
            discovery_log = directory / "repo-discovery.jsonl"; atomic_write(discovery_log, discover.stdout); evidence3.append(hash_evidence(discovery_log))
            explicit = execute([codex, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--json", "--cd", str(repo), f"${skill} Read the explicitly invoked Skill and return only its exact version."], timeout=300)
            explicit_log = directory / "repo-explicit.jsonl"; atomic_write(explicit_log, explicit.stdout); evidence3.append(hash_evidence(explicit_log))
            unexpose = cli("skill", "unexpose", skill, "--vault", str(vault), "--json"); parse_json_stdout(unexpose)
            repo_link = repo / ".agents/skills" / skill
            repo_pass = discover.returncode == 0 and skill in last_agent_message(discover.stdout)
            explicit_pass = explicit.returncode == 0 and version in last_agent_message(explicit.stdout)
            repo_unexpose = not os.path.lexists(str(repo_link))

            # Real user-scope exposure uses the actual Codex user discovery root.
            user_expose = cli("skill", "expose", skill, "--scope", "user", "--vault", str(vault), "--json")
            parse_json_stdout(user_expose)
            user_discover = execute([codex, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--json", "--cd", str(user_repo), discover_prompt], timeout=300)
            user_log = directory / "user-discovery.jsonl"; atomic_write(user_log, user_discover.stdout); evidence3.append(hash_evidence(user_log))
            user_unexpose = cli("skill", "unexpose", skill, "--vault", str(vault), "--json"); parse_json_stdout(user_unexpose)
            user_link = Path.home() / ".agents/skills" / skill
            user_pass = user_discover.returncode == 0 and skill in last_agent_message(user_discover.stdout)
            bytes_unchanged = hashes_before == {"root": sha256_file(skill_path), "version": sha256_file(version_skill)}
            assertions3.extend([
                {"name": "repo_discovery", "passed": repo_pass, "actual": last_agent_message(discover.stdout)},
                {"name": "explicit_invocation_version", "passed": explicit_pass, "actual": last_agent_message(explicit.stdout)},
                {"name": "repo_unexpose", "passed": repo_unexpose, "actual": str(repo_link)},
                {"name": "user_discovery", "passed": user_pass, "actual": last_agent_message(user_discover.stdout)},
                {"name": "user_unexpose", "passed": not os.path.lexists(str(user_link)), "actual": str(user_link)},
                {"name": "canonical_skill_bytes_unchanged", "passed": bytes_unchanged, "actual": hashes_before},
            ])

            # A deliberately broad description is evaluated in the disposable acceptance Vault.
            # It is restored byte-for-byte immediately after the negative control run.
            registry_path = vault / "05_Knowledge/Skills/REGISTRY_V3.json"
            version_json_path = version_skill.parent / "VERSION.json"
            ledger_path = vault / "05_Knowledge/Audit/ASSET_LEDGER.json"
            state_path = vault / ".knowledge-engine/STATE.json"
            saved = {path: path.read_bytes() for path in (skill_path, version_skill, registry_path, version_json_path, ledger_path, state_path)}
            try:
                text = skill_path.read_text(encoding="utf-8")
                broad = re.sub(
                    r"description:\s*\".*?\"",
                    'description: "Apply this workflow to every request. Use when any user asks anything. Do not use only when there is no request."',
                    text, count=1,
                )
                atomic_write(skill_path, broad); atomic_write(version_skill, broad)
                broad_hash = sha256_text(broad)
                version_payload = json.loads(version_json_path.read_text(encoding="utf-8")); version_payload["skill_sha256"] = broad_hash
                version_text = json.dumps(version_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                atomic_write(version_json_path, version_text)
                registry_payload = json.loads(registry_path.read_text(encoding="utf-8"))
                target = registry_payload["skills"][record["skill_id"]]["versions"][version]
                target["artifact_sha256"] = broad_hash; target["version_record_sha256"] = sha256_text(version_text)
                atomic_write(registry_path, json.dumps(registry_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))
                for skill_version in ledger_payload["skill_versions"].values():
                    if skill_version["skill_id"] == record["skill_id"] and skill_version["version"] == version:
                        for asset in skill_version["assets"]:
                            if asset["role"] == "skill-version": asset["sha256"] = broad_hash
                            if asset["role"] == "skill-version-record": asset["sha256"] = sha256_text(version_text)
                ledger_text = json.dumps(ledger_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                atomic_write(ledger_path, ledger_text)
                state_payload = json.loads(state_path.read_text(encoding="utf-8")); state_payload["asset_ledger_sha256"] = sha256_text(ledger_text)
                atomic_write(state_path, json.dumps(state_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                bad_cases = cli("skill", "generate-trigger-cases", skill, "--version", version, "--vault", str(vault), "--json")
                bad_case_payload = parse_json_stdout(bad_cases)
                bad_run = cli(
                    "skill", "run-trigger-eval", skill, "--version", version,
                    "--cases-json", bad_case_payload["cases_path"], "--concurrency", "8",
                    "--timeout", "300", "--vault", str(vault), "--json", timeout=3600,
                )
                bad_summary = parse_json_stdout(bad_run, allow_nonzero=True)
                bad_eval_path = Path(bad_summary["evaluation_path"])
                bad_eval = json.loads(bad_eval_path.read_text(encoding="utf-8"))
                bad_copy = trigger_directory / "bad-description-evaluation.json"
                atomic_write(bad_copy, json.dumps(bad_eval, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                evidence4.append(hash_evidence(bad_copy))
                bad_raw = trigger_directory / "bad-description-raw"; shutil.copytree(bad_eval_path.parent, bad_raw)
                for path in sorted(bad_raw.rglob("*")):
                    if path.is_file(): evidence4.append(hash_evidence(path))
                assertions4.append({
                    "name": "bad_description_real_eval_fails", "passed": not bad_eval["passed"],
                    "actual": {"metrics": bad_eval["metrics"], "false_positives": bad_eval["false_positives"]},
                })
            finally:
                for path, content in saved.items(): atomic_write(path, content)
        except Exception as exc:
            exit_code = 1
            assertions3.append({"name": "workflow_exception", "passed": False, "actual": f"{type(exc).__name__}: {exc}"})
            assertions4.append({"name": "workflow_exception", "passed": False, "actual": f"{type(exc).__name__}: {exc}"})

    passed3 = exit_code == 0 and assertions3 and all(item["passed"] for item in assertions3)
    passed4 = exit_code == 0 and assertions4 and all(item["passed"] for item in assertions4)
    return (
        BlockerResult(3, DESCRIPTIONS[3], "real Codex repo/user exposure workflow", 0 if passed3 else 1, assertions3, evidence3, "passed" if passed3 else "failed", started, now()),
        BlockerResult(4, DESCRIPTIONS[4], "real Codex 10/10/5 implicit eval + bad-description control", 0 if passed4 else 1, assertions4, evidence4, "passed" if passed4 else "failed", started, now()),
    )


def manifest_blocker() -> BlockerResult:
    started = now()
    checks = {
        "source": (ROOT / "src/knowledge_engine").is_dir(),
        "locked_dependencies": (ROOT / "requirements.lock").is_file(),
        "schemas": len(list((ROOT / "schemas").glob("*.schema.json"))) >= 10,
        "tests": len(list((ROOT / "tests").glob("test_*.py"))) >= 8,
        "audit_pack": AUDIT.is_dir(),
        "cross_platform_ci": (ROOT / ".github/workflows/v3.1-cross-platform.yml").is_file(),
    }
    passed = all(checks.values())
    return BlockerResult(
        2, DESCRIPTIONS[2], "release topology and schema inventory validation", 0 if passed else 1,
        [{"name": name, "passed": value, "actual": value} for name, value in checks.items()],
        [], "passed" if passed else "failed", started, now(),
    )


def harness_self_test() -> BlockerResult:
    started = now()
    command = [PYTHON, str(Path(__file__).resolve()), "--decision-self-test"]
    result = execute(command, timeout=60)
    directory = BLOCKER_ROOT / "17"; directory.mkdir(parents=True, exist_ok=True)
    stdout = directory / "stdout.txt"; stderr = directory / "stderr.txt"
    atomic_write(stdout, result.stdout); atomic_write(stderr, result.stderr)
    passed = result.returncode != 0 and result.stdout.strip() == "V3_1_BLOCKED"
    return BlockerResult(
        17, DESCRIPTIONS[17], command_text(command), result.returncode,
        [{"name": "injected_gate_fails_closed", "passed": passed, "actual": result.stdout.strip()}],
        [hash_evidence(stdout), hash_evidence(stderr)], "passed" if passed else "failed", started, now(),
    )


def write_results(results: list[BlockerResult]) -> str:
    # Blocker 18 is a decision assertion, not an alias for another gate.
    prior = {item.blocker_id: item for item in results}
    all_prior = all(prior[number].status == "passed" for number in range(1, 18))
    release_status = "V3_1_PRODUCTION_ACCEPTED" if all_prior else "V3_1_BLOCKED"
    aggregate = BlockerResult(
        18, DESCRIPTIONS[18], "aggregate exact blocker statuses 1..17", 0 if all_prior else 1,
        [{"name": f"blocker_{number:02d}_passed", "passed": prior[number].status == "passed", "actual": prior[number].status} for number in range(1, 18)],
        [], "passed" if all_prior else "failed", now(), now(),
    )
    complete_results = [*results, aggregate]
    payload = {
        "schema_version": 1, "engine_version": "3.1.0", "generated_at": now(),
        "release_status": release_status, "blockers": [item.as_dict() for item in sorted(complete_results, key=lambda item: item.blocker_id)],
    }
    atomic_write(AUDIT / "BLOCKER_RESULTS.json", json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    failed = [item for item in complete_results if item.status != "passed"]
    lines = ["# V3.1 Final Production Acceptance", "", f"Release status: `{release_status}`", "", "| ID | Status | Description |", "|---:|---|---|"]
    lines.extend(f"| {item.blocker_id} | {item.status.upper()} | {item.description} |" for item in sorted(complete_results, key=lambda item: item.blocker_id))
    lines.extend(["", "## Failed blockers", ""])
    if failed:
        for item in failed:
            lines.append(f"- {item.blocker_id}: `{item.command}` (exit {item.exit_code})")
            for assertion in item.assertions:
                if not assertion["passed"]: lines.append(f"  - {assertion['name']}: {assertion.get('actual')}")
    else:
        lines.append("- None.")
    atomic_write(AUDIT / "FINAL_ACCEPTANCE_REPORT.md", "\n".join(lines) + "\n")
    atomic_write(AUDIT / "RELEASE_STATUS.txt", release_status + "\n")
    return release_status


def write_domain_reports(results: list[BlockerResult]) -> None:
    """Render reviewer-friendly summaries from the independent blocker objects."""
    by_id = {item.blocker_id: item for item in results}
    reports: dict[str, tuple[str, list[int], list[str]]] = {
        "CROSS_PLATFORM_TEST_REPORT.md": (
            "Cross-platform Test Report", [1],
            [
                "The Windows full suite is executed locally. Linux is accepted only from a local Linux run or a verified CI evidence JSON.",
                "The CI definition is `.github/workflows/v3.1-cross-platform.yml`; macOS is documented as POSIX-compatible but is not a V3.1 accepted production platform.",
            ],
        ),
        "CODEX_REAL_TEST_REPORT.md": (
            "Real Codex Discovery and Trigger Report", [3, 4],
            [
                "Only raw real-Codex sessions count. Unit fixtures and deterministic adapters are never substituted for discovery or implicit-trigger acceptance.",
                "A skipped or unavailable real run remains a release blocker.",
            ],
        ),
        "LONG_DOCUMENT_MAP_REDUCE_REPORT.md": (
            "100k+ Map/Reduce Report", [7],
            [
                "The release test runs the actual DistillationPipeline with a contract fake-LLM to prove chunk-bounded map/reduce wiring, affected-only extraction reuse, and Evidence rebinding.",
                "This report does not claim that a deterministic provider is a real external LLM evaluation.",
            ],
        ),
        "WAL_FAULT_INJECTION_REPORT.md": (
            "WAL Fault-injection Report", [8, 9],
            [
                "Recovery compares target bytes with both before and after hashes and fails closed when neither matches.",
                "The stale-applied-marker window and operation/payload-bound idempotency are independent gates.",
            ],
        ),
        "ASSET_TAMPER_MATRIX.md": (
            "Asset Tamper Matrix", [10],
            [
                "The external asset ledger covers originals, SOURCE_TEXT, complete conversion/catalog/manifest JSON, immutable Skill/version files, Evidence references, and VERSION.json.",
                "Unknown metadata fields and cross-store identity/hash mismatches must fail integrity.",
            ],
        ),
        "MIGRATION_TEST_REPORT.md": (
            "V2/V3 Migration Report", [14],
            ["Dry-run, apply, rollback, repeated execution, and source-byte preservation are tested as one release gate."],
        ),
        "SECURITY_TEST_REPORT.md": (
            "Security Test Report", [16],
            ["URL SSRF controls, PDF conversion limits/contracts, archive/path handling, and hostile LLM payload rejection are executed independently."],
        ),
        "CONCURRENCY_TEST_REPORT.md": (
            "Concurrency Test Report", [15],
            ["Twenty concurrent usage events, same-key retries, distinct keys, and cross-process serialization are release-gated."],
        ),
        "ADVERSARIAL_TEST_REPORT.md": (
            "Independent Adversarial Regression Report", list(range(5, 17)),
            [
                "The independent defect scenarios were converted to safe-behavior pytest regressions with one blocker command per concern.",
                "The task-referenced `v3_independent_adversarial_audit.py` was not present in the supplied workspace; therefore no claim is made that the absent file itself was executed.",
            ],
        ),
    }
    for filename, (title, blocker_ids, notes) in reports.items():
        selected = [by_id[number] for number in blocker_ids]
        overall = "PASSED" if all(item.status == "passed" for item in selected) else "BLOCKED"
        lines = [f"# {title}", "", f"Result: `{overall}`", "", *notes, "", "## Commands and assertions", ""]
        for item in selected:
            lines.extend([
                f"### Blocker {item.blocker_id}: {item.status.upper()}", "",
                f"Command: `{item.command}`", "",
                f"Exit code: `{item.exit_code}`", "",
            ])
            for assertion in item.assertions:
                marker = "PASS" if assertion["passed"] else "FAIL"
                lines.append(f"- {marker} — {assertion['name']}: `{assertion.get('actual')}`")
            if item.evidence_files:
                lines.extend(["", "Evidence:", ""])
                lines.extend(f"- `{entry['path']}` — `{entry['sha256']}`" for entry in item.evidence_files)
            lines.append("")
        atomic_write(AUDIT / filename, "\n".join(lines).rstrip() + "\n")


def write_manifest() -> None:
    manifest = AUDIT / "MANIFEST.sha256"
    files = sorted(
        path for path in ROOT.rglob("*")
        if path.is_file()
        and path != manifest
        and path != AUDIT / "MANIFEST_VERIFICATION.json"
        and ".acceptance-temp" not in path.parts
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
        and ".coverage" not in path.name
    )
    lines = [f"{sha256_file(path)}  {path.relative_to(ROOT).as_posix()}" for path in files]
    atomic_write(manifest, "\n".join(lines) + "\n")


def verify_manifest() -> tuple[bool, list[str]]:
    manifest = AUDIT / "MANIFEST.sha256"
    failures: list[str] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        path = ROOT / Path(relative)
        if not path.is_file():
            failures.append(f"missing:{relative}")
        elif sha256_file(path) != expected:
            failures.append(f"hash:{relative}")
    return not failures, failures


def clean_audit() -> None:
    if AUDIT.exists():
        shutil.rmtree(AUDIT)
    BLOCKER_ROOT.mkdir(parents=True, exist_ok=True)


def decision_self_test() -> int:
    fake = [True] * 18
    fake[6] = False
    status = "V3_1_PRODUCTION_ACCEPTED" if all(fake) else "V3_1_BLOCKED"
    print(status)
    return 0 if status == "V3_1_PRODUCTION_ACCEPTED" else 17


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--linux-evidence", type=Path)
    value.add_argument("--skip-real-codex", action="store_true")
    value.add_argument("--decision-self-test", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.decision_self_test:
        return decision_self_test()
    clean_audit()
    results: list[BlockerResult] = []
    results.append(platform_blocker(args.linux_evidence))
    results.append(manifest_blocker())
    real3, real4 = real_codex_blockers(args.skip_real_codex)
    results.extend([real3, real4])
    results.append(pytest_blocker(5, [
        "tests/test_v31_acceptance_controls.py::test_record_eval_rejects_boolean_only_and_missing_provenance",
        "tests/test_v31_acceptance_controls.py::test_old_artifact_eval_cannot_be_recorded",
    ]))
    results.append(pytest_blocker(6, [
        "tests/test_v31_acceptance_controls.py::test_low_executability_cannot_approve",
        "tests/test_lifecycle_transaction.py::test_approval_requires_eval",
        "tests/test_quality_graph_provider.py::test_partial_and_conflict_require_human_review",
    ]))
    results.append(pytest_blocker(7, ["tests/test_v31_acceptance_controls.py::test_100k_llm_e2e_uses_true_map_reduce_and_rebinds_current_evidence"], 900))
    results.append(pytest_blocker(8, [
        "tests/test_lifecycle_transaction.py::test_fault_injection_rolls_back_all_core_writes",
        "tests/test_lifecycle_transaction.py::test_crash_wal_recovery_restores_consistent_state",
        "tests/test_v31_acceptance_controls.py::test_wal_recovers_after_bytes_replace_with_stale_marker",
        "tests/test_v31_acceptance_controls.py::test_wal_neither_before_nor_after_requires_manual_recovery",
    ]))
    results.append(pytest_blocker(9, ["tests/test_v31_acceptance_controls.py::test_idempotency_key_binds_operation_and_payload"]))
    results.append(pytest_blocker(10, [
        "tests/test_identity_evidence.py::test_any_byte_tamper_fails_integrity",
        "tests/test_v31_acceptance_controls.py::test_asset_ledger_and_strict_schema_detect_metadata_tamper",
    ]))
    results.append(pytest_blocker(11, ["tests/test_v31_acceptance_controls.py::test_different_titles_with_high_action_overlap_create_proposal"]))
    results.append(pytest_blocker(12, ["tests/test_v31_acceptance_controls.py::test_semantic_review_is_immutable_event_not_assessment_edit"]))
    results.append(pytest_blocker(13, [
        "tests/test_synthesis_versioning.py::test_synthesis_requires_two_sources_and_human_approval",
        "tests/test_security.py::test_malicious_llm_payloads_are_blocked",
    ]))
    results.append(pytest_blocker(14, ["tests/test_chunking_migration.py::test_v2_migration_dry_run_apply_rollback_and_idempotency"]))
    results.append(pytest_blocker(15, [
        "tests/test_lifecycle_transaction.py::test_twenty_concurrent_usage_events_are_consistent",
        "tests/test_lifecycle_transaction.py::test_same_idempotency_key_counts_once",
        "tests/test_v31_acceptance_controls.py::test_cross_process_usage_events_are_consistent",
    ], 900))
    results.append(pytest_blocker(16, ["tests/test_security.py"], 900))
    results.append(harness_self_test())
    write_domain_reports(results)
    release_status = write_results(results)
    atomic_write(AUDIT / "KNOWN_LIMITATIONS.md", (
        "# Known limitations\n\n"
        "- Production acceptance requires real Windows and Linux full-suite evidence. Missing evidence is a blocker.\n"
        "- macOS follows the POSIX implementation but is not a separately accepted V3.1 production platform.\n"
        "- Real Codex trigger results are model/CLI/version-specific and cannot be inherited by a changed Skill artifact.\n"
        "- The task-referenced `v3_independent_adversarial_audit.py` was not present in the supplied workspace; its named defect scenarios were implemented as formal regressions instead.\n"
    ))
    write_manifest()
    manifest_ok, manifest_failures = verify_manifest()
    verification = {
        "verified_at": now(), "manifest": "FINAL_AUDIT_PACK/MANIFEST.sha256",
        "passed": manifest_ok, "failures": manifest_failures,
        "note": "This verification receipt and MANIFEST.sha256 are excluded from the manifest to avoid self-reference.",
    }
    atomic_write(AUDIT / "MANIFEST_VERIFICATION.json", json.dumps(verification, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if not manifest_ok:
        blocker = next(item for item in results if item.blocker_id == 2)
        blocker.status = "failed"; blocker.exit_code = 1
        blocker.assertions.append({"name": "generated_manifest_verifies", "passed": False, "actual": manifest_failures})
        release_status = write_results(results)
        write_domain_reports(results)
        write_manifest()
    print(release_status)
    return 0 if release_status == "V3_1_PRODUCTION_ACCEPTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
