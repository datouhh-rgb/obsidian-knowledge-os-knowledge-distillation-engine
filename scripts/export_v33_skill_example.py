#!/usr/bin/env python3
"""Export the exact preserved V3.3 candidate as a review-only release example."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from knowledge_engine.registry import SkillRegistry
from run_v33_real_codex_integration import materialize_preserved_candidate


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-original", type=Path, required=True)
    parser.add_argument(
        "--upstream-regression", type=Path,
        default=ROOT / "release/evidence/regressions/stock_operator_v33_regression.json",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "release/SKILL_EXAMPLE")
    args = parser.parse_args()
    upstream = json.loads(args.upstream_regression.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="v33-skill-example-") as temporary:
        vault = Path(temporary) / "vault"
        (vault / "05_Knowledge").mkdir(parents=True)
        (vault / "06_Sources").mkdir()
        name, version, skill_id, chain = materialize_preserved_candidate(
            vault, args.source_original.resolve(), upstream,
        )
        record, version_record, lifecycle = SkillRegistry.load(vault).version(name, version)
        destination = args.output.resolve() / name
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(vault / "05_Knowledge/Skills" / name, destination)
        provenance = {
            "schema_version": 1, "engine_version": "3.3.0",
            "purpose": "release-review-example-not-exposed",
            "skill_id": skill_id, "name": name, "version": version,
            "status": lifecycle["status"], "automatic_approval": False,
            "exposed": False, "project_connections": [],
            "artifact_sha256": version_record["artifact_sha256"],
            "source_chain": chain,
            "upstream_regression": args.upstream_regression.relative_to(ROOT).as_posix(),
        }
        (args.output / "PROVENANCE.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({"skill": name, "status": lifecycle["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
