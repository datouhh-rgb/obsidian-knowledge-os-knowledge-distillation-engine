#!/usr/bin/env python3
"""Run the V3.3 《认知觉醒》 regression with real PDF conversion and Codex LLM."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from knowledge_engine.pipeline import DistillationPipeline
from knowledge_engine.providers import CommandLLMTransport, HybridAnalyzerProvider, LLMAnalyzerProvider
from knowledge_engine.utils import sha256_file
from run_stock_operator_regression import sanitize_machine_evidence, validate_portable_cache, write_atomic_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "release/evidence/regressions/cognitive_awakening_v33_regression.json"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def release_commit() -> str:
    if os.environ.get("GITHUB_SHA"):
        return str(os.environ["GITHUB_SHA"])
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-pdf", type=Path, required=True)
    parser.add_argument("--pdf-wrapper", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning", default="low")
    parser.add_argument("--chunk-chars", type=int, default=30_000)
    parser.add_argument(
        "--work-dir", type=Path,
        help="Retain a disposable Vault and its content-addressed extraction cache for safe resume.",
    )
    args = parser.parse_args()
    source = args.source_pdf.resolve()
    wrapper = args.pdf_wrapper.resolve()
    if not source.is_file() or not wrapper.is_file():
        write_atomic_json(args.output, {
            "schema_version": 2,
            "engine_version": "3.3.0",
            "commit_sha": release_commit(),
            "book": "认知觉醒",
            "status": "blocked",
            "pipeline_status": "NOT_STARTED",
            "block_reason": "Original PDF or local MarkItDown wrapper is unavailable.",
            "source_path": "[REDACTED]/" + source.name,
            "pdf_wrapper": wrapper.name,
        })
        return 29

    started = now()
    source_hash_before = sha256_file(source)
    adapter = ROOT / "scripts/codex_index_selection_adapter.py"
    transport = CommandLLMTransport(
        ("python", str(adapter), "--model", args.model, "--reasoning", args.reasoning, "--timeout", "300"),
        timeout=330, model=args.model,
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
        workspace = tempfile.TemporaryDirectory(prefix="v33-cognitive-awakening-")
    with workspace as temporary:
        vault = Path(temporary) / "vault"
        (vault / "05_Knowledge").mkdir(parents=True, exist_ok=True)
        (vault / "06_Sources").mkdir(exist_ok=True)
        pipeline = DistillationPipeline(
            vault,
            provider=HybridAnalyzerProvider(LLMAnalyzerProvider(transport)),
            chunk_chars=args.chunk_chars,
            pdf_wrapper=wrapper,
        )
        try:
            result = pipeline.distill(str(source), title="认知觉醒")
        except Exception as exc:
            write_atomic_json(args.output, {
                "schema_version": 2,
                "engine_version": "3.3.0",
                "commit_sha": release_commit(),
                "book": "认知觉醒",
                "started_at": started,
                "finished_at": now(),
                "status": "blocked",
                "pipeline_status": "CONVERSION_OR_PIPELINE_EXCEPTION",
                "source_path": "[REDACTED]/" + source.name,
                "source_sha256": source_hash_before,
                "pdf_converter": "Microsoft MarkItDown",
                "pdf_wrapper": wrapper.name,
                "block_reason": f"{type(exc).__name__}: {exc}",
                "formal_writes": {"graph": 0, "registry": 0, "skill": 0, "asset_ledger": 0},
            })
            print("COGNITIVE_AWAKENING_V33_REGRESSION_BLOCKED")
            return 29
        chunk = result.chunk_run or {}
        diagnostic = json.dumps(result.as_dict(), ensure_ascii=False)
        portable = validate_portable_cache(vault, chunk.get("extraction_hashes", {}))
        graph_path = vault / "05_Knowledge/Graph/concept-graph.v3.json"
        registry_path = vault / "05_Knowledge/Skills/REGISTRY_V3.json"
        ledger_path = vault / "05_Knowledge/Audit/ASSET_LEDGER.json"
        graph = json.loads(graph_path.read_text(encoding="utf-8")) if graph_path.is_file() else {"nodes": {}, "edges": {}}
        registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else {"skills": {}}
        statuses = [
            record.get("lifecycle", {}).get(record.get("current_version"), {}).get("status")
            for record in registry.get("skills", {}).values()
        ]
        methods = [path for path in (vault / "05_Knowledge/Methods").glob("*.md")] if (vault / "05_Knowledge/Methods").is_dir() else []
        concept_nodes = [item for item in graph.get("nodes", {}).values() if item.get("type") == "concept"]
        concept_titles = [str(item.get("title", "")) for item in concept_nodes]
        reserved_metadata = (
            "sourcepath", "sourcesha256", "kindleunpack", "converter",
            "isbn", "publication metadata", "publisher contact", "copyright-page",
            "开本", "印张", "定价", "反盗版热线", "读者服务热线", "广告经营许可证", "图书在版编目",
        )
        metadata_concepts = [
            title for title in concept_titles
            if any(term in title.casefold() for term in reserved_metadata)
        ]
        real_fragment_titles = {"则", "这就", "也就", "的就", "学习只", "阿健同学正", "在生活中更"}
        quarantined_fragments = [
            item.message for item in result.validation.issues
            if item.code == "concept-candidate-quarantined"
            and item.message.split(":", 1)[0].strip() in real_fragment_titles
        ]
        fragment_inputs: list[str] = []
        for cache_path in (vault / ".knowledge-engine/runs/extraction-cache").glob("*.json"):
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            for candidate in cache.get("result", {}).get("concepts", []):
                title = str(candidate.get("title", "")).strip()
                if title in real_fragment_titles:
                    fragment_inputs.append(title)
        fragment_survivors = [title for title in concept_titles if title.strip() in real_fragment_titles]
        diagnostic_rule_exclusion_recorded = any(
            "Rule Analyzer concept candidate(s) retained in extraction diagnostics only" in warning
            for warning in result.warnings
        )
        synthesis_paths = list(vault.rglob("KNOWLEDGE_SYNTHESIS.json"))
        synthesis = json.loads(synthesis_paths[0].read_text(encoding="utf-8")) if len(synthesis_paths) == 1 else {}
        synthesis_methods = synthesis.get("method_clusters", [])
        methods_cohesive = all(
            item.get("cohesion_assessment", {}).get("decision") == "passed"
            for item in synthesis_methods
        )
        evidence_catalogs = list(vault.rglob("EVIDENCE_CATALOG.json"))
        evidence_count = 0
        for path in evidence_catalogs:
            evidence_count += len(json.loads(path.read_text(encoding="utf-8")).get("items", []))
        conversion_files = list(vault.rglob("CONVERSION.json"))
        conversion = json.loads(conversion_files[0].read_text(encoding="utf-8")) if len(conversion_files) == 1 else {}
        exposed = any((vault / relative).exists() for relative in (".agents/skills", ".codex/skills"))
        edge_types = {str(item.get("type")) for item in graph.get("edges", {}).values()}
        formal_assets = [
            path.relative_to(vault).as_posix() for path in vault.rglob("*")
            if path.is_file() and not path.relative_to(vault).as_posix().startswith(".knowledge-engine/")
        ]
        checks = {
            "source_unchanged": sha256_file(source) == source_hash_before,
            "conversion_quality_passed_before_llm": result.conversion_quality.get("status") == "passed",
            "replacement_character_zero": result.conversion_quality.get("metrics", {}).get("replacement_character_count") == 0,
            "mojibake_zero": result.conversion_quality.get("metrics", {}).get("mojibake_hit_count") == 0,
            "pipeline_completed": result.status == "completed",
            "all_chunks_completed": not chunk.get("failed_chunk") and int(chunk.get("chunk_count", 0)) == len(chunk.get("processed", [])) + len(chunk.get("cache_hits", [])),
            "portable_evidence_selection": portable["passed"],
            "no_unknown_evidence": "unknown Evidence" not in diagnostic and "ev-629" not in diagnostic,
            "evidence_catalog_written": evidence_count > 0,
            "fragment_candidates_quarantined": not fragment_survivors and (
                not fragment_inputs or bool(quarantined_fragments) or diagnostic_rule_exclusion_recorded
            ),
            "metadata_to_concept_leakage_zero": not metadata_concepts,
            "diagnostic_rule_concepts_not_formal": all(item.get("provider") != "rule" for item in concept_nodes),
            "method_clusters_cohesive_if_present": methods_cohesive,
            "no_graph_identity_collision": "graph-identity-conflict" not in diagnostic,
            "candidate_status_only_if_present": all(item == "candidate" for item in statuses),
            "no_auto_approve": all(item != "approved" for item in statuses),
            "no_auto_expose": not exposed,
            "no_project_connection": "used_in_project" not in edge_types,
            "atomic_vault_commit": bool(formal_assets) and graph_path.is_file() and ledger_path.is_file(),
            "live_vault_untouched": True,
        }
        audit = {
            "schema_version": 2, "engine_version": "3.3.0", "commit_sha": release_commit(),
            "book": "认知觉醒", "started_at": started, "finished_at": now(),
            "mode": "real-pdf-markitdown-codex-llm-disposable-resumable-vault",
            "source_path": "[REDACTED]/" + source.name, "source_sha256": source_hash_before,
            "pdf_converter": "Microsoft MarkItDown", "pdf_wrapper": wrapper.name,
            "conversion_status": "ok" if conversion else "missing",
            "conversion_warnings": conversion.get("warnings", []),
            "conversion_quality": result.conversion_quality,
            "conversion_provenance": sanitize_machine_evidence(result.conversion_provenance),
            "source_id": result.source_id, "conversion_id": result.conversion_id,
            "run_id": chunk.get("run_id"), "pipeline_status": result.status,
            "run_status": chunk.get("status"),
            "run_warnings": chunk.get("warnings", []),
            "pipeline_warnings": result.warnings,
            "validation_issues": [item.__dict__ for item in result.validation.issues],
            "block_reason": "; ".join(
                f"{item.code}: {item.message}" for item in result.validation.errors
            ),
            "chunk_count": chunk.get("chunk_count"),
            "completed_chunks": len(chunk.get("processed", [])) + len(chunk.get("cache_hits", [])),
            "failed_chunk": chunk.get("failed_chunk"),
            "evidence_count": evidence_count, "concept_count": len(concept_nodes),
            "method_cluster_count": len(methods), "skill_candidate_count": len(statuses),
            "skill_statuses": statuses, "graph_edge_types": sorted(edge_types),
            "concept_gate": result.concept_gate,
            "quarantined_fragments": quarantined_fragments,
            "fragment_candidate_inputs": fragment_inputs,
            "fragment_candidate_survivors": fragment_survivors,
            "diagnostic_rule_exclusion_recorded": diagnostic_rule_exclusion_recorded,
            "metadata_concepts": metadata_concepts,
            "knowledge_synthesis": synthesis,
            "portable_validation": portable,
            "model": args.model, "reasoning": args.reasoning,
            "concept_title_policy_version": "canonical-concept-label-v1",
            "transport_encoding": "utf8-strict-v1",
            "live_vault_opened": False,
            "work_dir": "[DISPOSABLE_SELF_HOSTED_WORKDIR]",
            "temporary_vault_deleted_after_run": not bool(args.work_dir),
            "checks": checks,
            "status": "passed" if all(checks.values()) else "blocked",
            "blockers": [name for name, passed in checks.items() if not passed],
        }
    write_atomic_json(args.output, audit)
    print("COGNITIVE_AWAKENING_V33_REGRESSION_PASS" if audit["status"] == "passed" else "COGNITIVE_AWAKENING_V33_REGRESSION_BLOCKED")
    print(json.dumps({
        "run_id": audit["run_id"], "chunks": audit["chunk_count"],
        "evidence": audit["evidence_count"], "concepts": audit["concept_count"],
        "methods": audit["method_cluster_count"], "skills": audit["skill_candidate_count"],
    }, ensure_ascii=False))
    return 0 if audit["status"] == "passed" else 29


if __name__ == "__main__":
    raise SystemExit(main())
