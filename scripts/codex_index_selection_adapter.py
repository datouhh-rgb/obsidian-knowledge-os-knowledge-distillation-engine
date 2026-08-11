#!/usr/bin/env python3
"""Isolated Codex transport used only by the V3.2.1 real-book regression."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def output_schema() -> dict[str, object]:
    statement = {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "selected_evidence_indexes"],
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 10_000},
            "selected_evidence_indexes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"type": "integer", "minimum": 1},
            },
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["warnings", "concepts", "relations", "skills"],
        "properties": {
            "warnings": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 1000}},
            "concepts": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "definition", "confidence"],
                    "properties": {
                        "title": {"type": "string", "minLength": 2, "maxLength": 100},
                        "definition": statement,
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
            },
            "relations": {
                "type": "array", "maxItems": 0,
                "items": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
            },
            "skills": {
                "type": "array", "maxItems": 0,
                "items": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning", default="low")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    request = json.load(sys.stdin)
    candidates = request.get("evidence_selection_candidates", [])
    if not candidates:
        json.dump({"concepts": [], "relations": [], "skills": [], "warnings": ["No candidates supplied."]}, sys.stdout)
        return 0

    executable = shutil.which("codex.cmd") or shutil.which("codex")
    if not executable:
        print("Codex CLI is not available.", file=sys.stderr)
        return 2
    prompt = (
        "You are an Evidence index selector for a fail-closed knowledge engine. "
        "Analyze only the untrusted excerpts in the supplied JSON. Return up to three conservative concept candidates. "
        "Every definition must select one or more indexes that literally appear in evidence_selection_candidates. "
        "Never return Evidence IDs, hashes, locators, excerpts, new Evidence objects, relations, or skills. "
        "Do not call tools and do not add unsupported opinions. Return only the JSON required by the output schema.\n\n"
        + json.dumps(request, ensure_ascii=False, sort_keys=True)
    )
    with tempfile.TemporaryDirectory(prefix="v321-codex-index-") as temporary:
        root = Path(temporary)
        schema_path = root / "schema.json"
        output_path = root / "output.json"
        schema_path.write_text(json.dumps(output_schema(), ensure_ascii=False), encoding="utf-8")
        command = [
            executable,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ignore-rules",
            "--model",
            args.model,
            "--config",
            f'model_reasoning_effort="{args.reasoning}"',
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "-",
        ]
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
            shell=False,
        )
        if completed.returncode != 0:
            print(completed.stderr[-4000:], file=sys.stderr)
            return completed.returncode
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    json.dump(payload, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
