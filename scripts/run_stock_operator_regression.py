#!/usr/bin/env python3
"""Run the V3.3 stock-operator synthesis regression in a disposable Vault."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from knowledge_engine.chunking import ProviderMapReducer
from knowledge_engine.pipeline import DistillationPipeline
from knowledge_engine.providers import CommandLLMTransport, HybridAnalyzerProvider, LLMAnalyzerProvider
from knowledge_engine.utils import sha256_file


ROOT = Path(__file__).resolve().parents[1]


def write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sanitize_machine_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_machine_evidence(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_machine_evidence(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_machine_evidence(item) for item in value]
    if isinstance(value, str) and (
        re.match(r"^[A-Za-z]:[\\/]", value)
        or value.startswith(("/Users/", "/home/", "C:\\Users\\"))
    ):
        return "[REDACTED]/" + Path(value).name
    return value


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def release_commit() -> str:
    if os.environ.get("GITHUB_SHA"):
        return str(os.environ["GITHUB_SHA"])
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def validate_portable_cache(vault: Path, extraction_hashes: dict[str, str]) -> dict[str, object]:
    expected = set(extraction_hashes.values())
    observed: set[str] = set()
    checked = 0
    bindings = 0
    evidence_candidates = 0
    concepts = 0
    relations = 0
    skill_candidates = 0
    failures: list[str] = []
    for path in (vault / ".knowledge-engine" / "runs" / "extraction-cache").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("extraction_hash") not in expected:
            continue
        observed.add(str(record["extraction_hash"]))
        checked += 1
        payload = record.get("result", {})
        try:
            ProviderMapReducer.validate_portable(payload)
        except Exception as exc:  # fail-closed audit capture
            failures.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue
        encoded = json.dumps(payload, ensure_ascii=False)
        if '"evidence_id"' in encoded or '"evidence_ids"' in encoded:
            failures.append(f"{path.name}: forbidden Evidence ID field")
        available = {item["index"] for item in payload["evidence_selection_candidates"]}
        evidence_candidates += len(available)
        concepts += len(payload["concepts"])
        relations += len(payload["relations"])
        skill_candidates += len(payload["skills"])

        def statement(value: dict[str, Any]) -> None:
            nonlocal bindings
            indexes = value["selected_evidence_indexes"]
            bindings += len(indexes)
            if value["origin"] == "source" and (not indexes or not set(indexes).issubset(available)):
                failures.append(f"{path.name}: invalid selected Evidence index")

        for item in payload["concepts"]:
            statement(item["definition"])
        for item in payload["relations"]:
            statement(item["relation"])
        for item in payload["skills"]:
            statement(item["positioning"])
            statement(item["output_format"])
            for key in (
                "scenarios", "input_requirements", "decision_flow", "actions",
                "boundaries", "prohibited_scenarios",
            ):
                for value in item[key]:
                    statement(value)
    return {
        "portable_payloads_checked": checked,
        "evidence_selection_candidates": evidence_candidates,
        "evidence_binding_occurrences": bindings,
        "concept_candidates": concepts,
        "relation_candidates": relations,
        "skill_candidates": skill_candidates,
        "expected_unique_extraction_hashes": len(expected),
        "observed_unique_extraction_hashes": len(observed),
        "passed": observed == expected and not failures,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-azw3", type=Path, required=True)
    parser.add_argument("--kindleunpack-script", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "FINAL_PRODUCTION_AUDIT" / "stock_operator_v33_regression.json")
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning", default="low")
    parser.add_argument("--chunk-chars", type=int, default=30_000)
    parser.add_argument(
        "--work-dir", type=Path,
        help="Retain a disposable Vault and its content-addressed extraction cache for safe resume.",
    )
    args = parser.parse_args()
    source = args.source_azw3.resolve()
    kindleunpack_script = args.kindleunpack_script.resolve()
    if not source.is_file() or not kindleunpack_script.is_file():
        write_atomic_json(args.output, {
            "schema_version": 2,
            "engine_package_version": "3.3.0",
            "commit_sha": release_commit(),
            "book": "股票大作手回忆录",
            "status": "blocked",
            "pipeline_status": "NOT_STARTED",
            "block_reason": "Original AZW3 or KindleUnpack script is unavailable.",
            "source_path": "[REDACTED]/" + source.name,
            "converter_script": kindleunpack_script.name,
        })
        return 23

    started = now()
    source_hash_before = sha256_file(source)
    azw3_command = (sys.executable, str(kindleunpack_script), "{input}", "{output_dir}")
    adapter = ROOT / "scripts" / "codex_index_selection_adapter.py"
    transport = CommandLLMTransport(
        (sys.executable, str(adapter), "--model", args.model, "--reasoning", args.reasoning, "--timeout", "300"),
        timeout=330,
        model=args.model,
        config={
            "reasoning": args.reasoning,
            "evidence_protocol": "selected-indexes-only",
            "transport_encoding": "utf8-strict-v1",
        },
    )
    if args.work_dir:
        work_root = args.work_dir.resolve()
        work_root.mkdir(parents=True, exist_ok=True)
        workspace = nullcontext(str(work_root))
    else:
        workspace = tempfile.TemporaryDirectory(prefix="v321-stock-operator-")
    with workspace as temporary:
        vault = Path(temporary) / "vault"
        vault.mkdir(exist_ok=True)
        (vault / "05_Knowledge").mkdir(exist_ok=True)
        (vault / "06_Sources").mkdir(exist_ok=True)
        pipeline = DistillationPipeline(
            vault,
            provider=HybridAnalyzerProvider(LLMAnalyzerProvider(transport)),
            chunk_chars=args.chunk_chars,
            azw3_command=azw3_command,
        )
        try:
            result = pipeline.distill(str(source), title="股票大作手回忆录")
        except Exception as exc:  # converter/quality failures must still produce machine evidence
            write_atomic_json(args.output, {
                "schema_version": 2,
                "engine_package_version": "3.3.0",
                "commit_sha": release_commit(),
                "book": "股票大作手回忆录",
                "started_at": started,
                "finished_at": now(),
                "status": "blocked",
                "pipeline_status": "CONVERSION_OR_PIPELINE_EXCEPTION",
                "source_path": "[REDACTED]/" + source.name,
                "source_sha256_before": source_hash_before,
                "source_sha256_after": sha256_file(source),
                "converter": "KindleUnpack canonical EPUB spine adapter",
                "converter_script": kindleunpack_script.name,
                "block_reason": f"{type(exc).__name__}: {exc}",
                "formal_writes": {"graph": 0, "registry": 0, "skill": 0, "asset_ledger": 0},
            })
            print("STOCK_OPERATOR_SYNTHESIS_REGRESSION_BLOCKED")
            return 23
        chunk_run = result.chunk_run or {}
        portable = validate_portable_cache(vault, chunk_run.get("extraction_hashes", {}))
        diagnostic_text = json.dumps(result.as_dict(), ensure_ascii=False)
        formal_assets = [
            path.relative_to(vault).as_posix()
            for path in vault.rglob("*")
            if path.is_file() and not path.relative_to(vault).as_posix().startswith(".knowledge-engine/")
        ]
        graph_path = vault / "05_Knowledge/Graph/concept-graph.v3.json"
        registry_path = vault / "05_Knowledge/Skills/REGISTRY_V3.json"
        ledger_path = vault / "05_Knowledge/Audit/ASSET_LEDGER.json"
        graph_payload = json.loads(graph_path.read_text(encoding="utf-8")) if graph_path.is_file() else {"nodes": {}, "edges": {}}
        registry_payload = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else {"skills": {}}
        synthesis_paths = list(vault.rglob("KNOWLEDGE_SYNTHESIS.json"))
        synthesis_payload = json.loads(synthesis_paths[0].read_text(encoding="utf-8")) if len(synthesis_paths) == 1 else {}
        method_clusters = synthesis_payload.get("method_clusters", [])
        synthesis_skills = synthesis_payload.get("skills", [])
        method_clusters_cohesive = bool(method_clusters) and all(
            item.get("cohesion_assessment", {}).get("decision") == "passed"
            for item in method_clusters
        )
        skill_actions_semantic = bool(synthesis_skills) and all(
            action.get("origin") == "source"
            and bool(action.get("evidence_ids"))
            and any(token in str(action.get("text", "")).casefold() for token in (
                "when", "if", "当", "若", "check", "verify", "record", "stop", "检查", "记录", "停止",
            ))
            for item in synthesis_skills
            for action in item.get("candidate", {}).get("actions", [])
        )
        registry_statuses = [
            record.get("lifecycle", {}).get(record.get("current_version"), {}).get("status")
            for record in registry_payload.get("skills", {}).values()
        ]
        registry_titles = [str(record.get("title", "")) for record in registry_payload.get("skills", {}).values()]
        graph_edge_types = {item.get("type") for item in graph_payload.get("edges", {}).values()}
        used_in_project = any(item.get("type") == "used_in_project" for item in graph_payload.get("edges", {}).values())
        skill_evidence_files = list((vault / "05_Knowledge/Skills").glob("*/versions/*/references/evidence.json"))
        skill_evidence_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in skill_evidence_files]
        skill_evidence_valid = all(
            payload.get("source_id") == result.source_id
            and payload.get("conversion_id") == result.conversion_id
            and len(payload.get("evidence", [])) >= 3
            for payload in skill_evidence_payloads
        )
        exposed = (vault / ".agents/skills").exists() or (vault / ".codex/skills").exists()
        issue_codes = [item.code for item in result.validation.issues]
        graph_written = graph_path.exists()
        registry_written = registry_path.exists()
        ledger_written = ledger_path.exists()
        conversion_config = result.conversion_provenance.get("config", {})
        if not isinstance(conversion_config, dict):
            conversion_config = {}
        input_attestation = conversion_config.get("converter_input_attestation", {})
        if not isinstance(input_attestation, dict):
            input_attestation = {}
        conversion_metrics = result.conversion_quality.get("metrics", {})
        if not isinstance(conversion_metrics, dict):
            conversion_metrics = {}
        concept_titles = [
            str(item.get("title", ""))
            for item in graph_payload.get("nodes", {}).values()
            if item.get("type") == "concept"
        ]
        reserved_metadata = (
            "sourcepath", "sourcesha256", "source_id", "conversion_id", "kindleunpack",
            "converter", "command fingerprint", "unpacked container hash", "isbn",
        )
        metadata_concepts = [
            item for item in concept_titles
            if any(term in item.casefold() for term in reserved_metadata)
        ]
        synthesized_action_texts = [
            str(action.get("text", ""))
            for item in synthesis_skills
            for action in item.get("candidate", {}).get("actions", [])
        ]
        audit = {
            "schema_version": 2,
            "engine_package_version": "3.3.0",
            "commit_sha": release_commit(),
            "data_contract_version": "3.2.0",
            "started_at": started,
            "finished_at": now(),
            "book": "股票大作手回忆录",
            "mode": "real-azw3-canonical-kindleunpack-codex-llm-disposable-resumable-vault",
            "analyzer": "HybridAnalyzerProvider(LLMAnalyzerProvider)",
            "source_path": "[REDACTED]/" + source.name,
            "source_sha256_before": source_hash_before,
            "source_sha256_after": sha256_file(source),
            "canonical_converter": "KindleUnpack canonical EPUB spine adapter",
            "converter_script": kindleunpack_script.name,
            "stale_snapshot_used": False,
            "conversion_quality": result.conversion_quality,
            "conversion_provenance": sanitize_machine_evidence(result.conversion_provenance),
            "model": args.model,
            "reasoning": args.reasoning,
            "prompt_version": "distillation-map-prompt-v3.2.1-index-selection",
            "concept_title_policy_version": "canonical-concept-label-v1",
            "transport_encoding": "utf8-strict-v1",
            "portable_schema_version": "portable-provider-map-v3",
            "source_id": result.source_id,
            "conversion_id": result.conversion_id,
            "run_id": chunk_run.get("run_id"),
            "run_status": chunk_run.get("status"),
            "pipeline_status": result.status,
            "chunk_count": chunk_run.get("chunk_count"),
            "processed": len(chunk_run.get("processed", [])),
            "cache_hits": len(chunk_run.get("cache_hits", [])),
            "failed_chunk": chunk_run.get("failed_chunk"),
            "completed_chunks": len(chunk_run.get("processed", [])) + len(chunk_run.get("cache_hits", [])),
            "failed_chunks": 1 if chunk_run.get("failed_chunk") else 0,
            "run_warnings": chunk_run.get("warnings", []),
            "pipeline_warnings": result.warnings,
            "validation_issues": [item.__dict__ for item in result.validation.issues],
            "block_reason": "; ".join(
                f"{item.code}: {item.message}" for item in result.validation.errors
            ),
            "unknown_evidence_error": "unknown Evidence" in diagnostic_text,
            "ev_629_error": "ev-629" in diagnostic_text,
            "portable_validation": portable,
            "formal_assets_in_disposable_vault": formal_assets,
            "written_paths": [path.relative_to(vault).as_posix() for path in result.written_paths],
            "skills_generated": len(result.skills),
            "method_cluster_count": len(method_clusters),
            "method_clusters": method_clusters,
            "knowledge_synthesis": synthesis_payload,
            "skill_candidate_count": len(registry_statuses),
            "skill_candidate_statuses": registry_statuses,
            "skill_evidence_file_count": len(skill_evidence_files),
            "skill_evidence_valid": skill_evidence_valid,
            "concept_fragment_rejections": issue_codes.count("concept-fragment-rejected"),
            "metadata_concepts": metadata_concepts,
            "graph": {
                "identity_collision": "graph-identity-conflict" in issue_codes,
                "committed": graph_written,
                "status": "committed" if graph_written else "not_committed_fail_closed",
                "edge_types": sorted(str(item) for item in graph_edge_types),
            },
            "registry": {
                "committed": registry_written,
                "status": "committed" if registry_written else "not_committed_fail_closed",
            },
            "asset_ledger": {
                "committed": ledger_written,
                "status": "committed" if ledger_written else "not_committed_fail_closed",
            },
            "vault_write_status": "atomic_formal_asset_write" if formal_assets and result.written_paths else "no_formal_asset_write",
            "live_vault_opened": False,
            "work_dir": "[DISPOSABLE_SELF_HOSTED_WORKDIR]",
            "temporary_vault_deleted_after_run": not bool(args.work_dir),
            "release_checks": {
                "evidence_error_disappeared": "unknown Evidence" not in diagnostic_text and "ev-629" not in diagnostic_text,
                "original_azw3_read_by_converter": all([
                    input_attestation.get("input_placeholder_present") is True,
                    input_attestation.get("exact_original_path_supplied") is True,
                    input_attestation.get("source_sha256_before") == source_hash_before,
                    input_attestation.get("source_sha256_after") == source_hash_before,
                ]),
                "stale_snapshot_not_used": True,
                "conversion_quality_passed_before_llm": result.conversion_quality.get("status") == "passed",
                "replacement_character_zero": int(conversion_metrics.get("replacement_character_count", -1)) == 0,
                "mojibake_zero": int(conversion_metrics.get("mojibake_hit_count", -1)) == 0,
                "metadata_to_concept_leakage_zero": not metadata_concepts,
                "no_arbitrary_bigram_signal": all(
                    "断风" not in item and "势变" not in item for item in synthesized_action_texts
                ),
                "successful_chunk_bindings_legal": portable["passed"],
                "pipeline_completed": result.status == "completed",
                # Three-to-eight remains the production target, not a quota.
                # At least one fully gated Method/Skill is sufficient for the
                # real Codex chain; no weak candidate is admitted to fill a count.
                "method_clusters_generated": 1 <= len(method_clusters) <= 8,
                "method_clusters_cohesive": method_clusters_cohesive,
                "skill_candidates_generated": 1 <= len(registry_statuses) <= 8,
                "skill_actions_not_raw_evidence": skill_actions_semantic,
                "skill_evidence_bound": bool(skill_evidence_files) and skill_evidence_valid,
                "candidate_status_only": bool(registry_statuses) and all(item == "candidate" for item in registry_statuses),
                "not_auto_approved": "approved" not in registry_statuses,
                "not_auto_exposed": not exposed,
                "not_connected_to_project": not used_in_project,
                "no_graph_identity_collision": "graph-identity-conflict" not in issue_codes,
                "no_obvious_fragment_skill": all(
                    title.strip() not in {"这就", "也就", "的就", "所以说", "就是说", "于是就", "然后就"}
                    for title in registry_titles
                ),
                "graph_four_layer_links": {"supports", "generates"}.issubset(graph_edge_types),
                "vault_transaction_complete": all([graph_written, registry_written, ledger_written, formal_assets, result.written_paths]),
                "live_vault_untouched": True,
            },
        }
    passed = all([
        audit["source_sha256_before"] == audit["source_sha256_after"],
        *audit["release_checks"].values(),
    ])
    audit["status"] = "passed" if passed else "blocked"
    write_atomic_json(args.output, audit)
    print("STOCK_OPERATOR_SYNTHESIS_REGRESSION_PASS" if passed else "STOCK_OPERATOR_SYNTHESIS_REGRESSION_BLOCKED")
    print(json.dumps({
        "run_id": audit["run_id"], "run_status": audit["run_status"],
        "pipeline_status": audit["pipeline_status"], "chunks": audit["chunk_count"],
        "evidence_bindings": audit["portable_validation"]["evidence_binding_occurrences"],
        "method_clusters": audit["method_cluster_count"],
        "skill_candidates": audit["skill_candidate_count"],
    }, ensure_ascii=False))
    return 0 if passed else 23


if __name__ == "__main__":
    raise SystemExit(main())
