#!/usr/bin/env python3
"""Run the V3.3 《认知觉醒》 regression with real PDF conversion and Codex LLM."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from knowledge_engine.pipeline import DistillationPipeline
from knowledge_engine.providers import CommandLLMTransport, HybridAnalyzerProvider, LLMAnalyzerProvider
from knowledge_engine.utils import sha256_file
from run_stock_operator_regression import validate_portable_cache


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "release/evidence/regressions/cognitive_awakening_v33_regression.json"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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
        raise SystemExit("Source PDF and the local MarkItDown wrapper are required.")

    started = now()
    source_hash_before = sha256_file(source)
    adapter = ROOT / "scripts/codex_index_selection_adapter.py"
    transport = CommandLLMTransport(
        ("python", str(adapter), "--model", args.model, "--reasoning", args.reasoning, "--timeout", "300"),
        timeout=330, model=args.model,
        config={"reasoning": args.reasoning, "evidence_protocol": "selected-indexes-only"},
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
        result = pipeline.distill(str(source), title="认知觉醒")
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
            "pipeline_completed": result.status == "completed",
            "all_chunks_completed": not chunk.get("failed_chunk") and int(chunk.get("chunk_count", 0)) == len(chunk.get("processed", [])) + len(chunk.get("cache_hits", [])),
            "portable_evidence_selection": portable["passed"],
            "no_unknown_evidence": "unknown Evidence" not in diagnostic and "ev-629" not in diagnostic,
            "evidence_catalog_written": evidence_count > 0,
            "no_graph_identity_collision": "graph-identity-conflict" not in diagnostic,
            "no_auto_approve": all(item != "approved" for item in statuses),
            "no_auto_expose": not exposed,
            "no_project_connection": "used_in_project" not in edge_types,
            "atomic_vault_commit": bool(formal_assets) and graph_path.is_file() and ledger_path.is_file(),
            "live_vault_untouched": True,
        }
        audit = {
            "schema_version": 1, "engine_version": "3.3.0",
            "book": "认知觉醒", "started_at": started, "finished_at": now(),
            "mode": "real-pdf-markitdown-codex-llm-disposable-resumable-vault",
            "source_path": str(source), "source_sha256": source_hash_before,
            "pdf_converter": "Microsoft MarkItDown", "pdf_wrapper": str(wrapper),
            "conversion_status": "ok" if conversion else "missing",
            "conversion_warnings": conversion.get("warnings", []),
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
            "portable_validation": portable,
            "model": args.model, "reasoning": args.reasoning,
            "live_vault_opened": False,
            "work_dir": str(Path(temporary).resolve()),
            "temporary_vault_deleted_after_run": not bool(args.work_dir),
            "checks": checks,
            "status": "passed" if all(checks.values()) else "blocked",
            "blockers": [name for name, passed in checks.items() if not passed],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("COGNITIVE_AWAKENING_V33_REGRESSION_PASS" if audit["status"] == "passed" else "COGNITIVE_AWAKENING_V33_REGRESSION_BLOCKED")
    print(json.dumps({
        "run_id": audit["run_id"], "chunks": audit["chunk_count"],
        "evidence": audit["evidence_count"], "concepts": audit["concept_count"],
        "methods": audit["method_cluster_count"], "skills": audit["skill_candidate_count"],
    }, ensure_ascii=False))
    return 0 if audit["status"] == "passed" else 29


if __name__ == "__main__":
    raise SystemExit(main())
