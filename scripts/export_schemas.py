#!/usr/bin/env python3
"""Export the executable V3 JSON Schemas from their authoritative code definitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_engine.graph import ConceptGraph
from knowledge_engine.gates import skill_candidate_schema
from knowledge_engine.ingest import SourceIngester
from knowledge_engine.ledger import AssetLedger
from knowledge_engine.providers import LLMAnalyzerProvider
from knowledge_engine.registry import SkillRegistry
from knowledge_engine.schemas_v31 import CONVERSION_SCHEMA, EVIDENCE_CATALOG_SCHEMA, MANIFEST_SCHEMA, SOURCE_IDENTITY_SCHEMA, VERSION_SCHEMA
from knowledge_engine.trigger_eval import TriggerEvalValidator


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = {
    "concept-graph.schema.json": ConceptGraph.schema(),
    "llm-candidate.schema.json": LLMAnalyzerProvider._schema(),
    "pdf-metadata.schema.json": SourceIngester._pdf_metadata_schema(),
    "skill-candidate.schema.json": skill_candidate_schema(),
    "skill-registry.schema.json": SkillRegistry.schema(),
    "conversion.schema.json": CONVERSION_SCHEMA,
    "source-identity.schema.json": SOURCE_IDENTITY_SCHEMA,
    "evidence-catalog.schema.json": EVIDENCE_CATALOG_SCHEMA,
    "distillation-manifest.schema.json": MANIFEST_SCHEMA,
    "skill-version.schema.json": VERSION_SCHEMA,
    "asset-ledger.schema.json": AssetLedger.schema(),
    "trigger-case-set.schema.json": TriggerEvalValidator.case_set_schema(),
    "trigger-eval.schema.json": TriggerEvalValidator.run_schema(),
}


def rendered_schemas() -> dict[str, bytes]:
    return {
        name: (json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        for name, schema in SCHEMAS.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail when committed schema files drift from code.")
    args = parser.parse_args()
    target = ROOT / "schemas"
    target.mkdir(parents=True, exist_ok=True)
    drift: list[str] = []
    for name, content in rendered_schemas().items():
        path = target / name
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                drift.append(name)
        else:
            path.write_bytes(content)
    if args.check:
        print(f"schema_drift={len(drift)} files={','.join(drift)}")
        return 1 if drift else 0
    print(f"Exported {len(SCHEMAS)} schemas to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
