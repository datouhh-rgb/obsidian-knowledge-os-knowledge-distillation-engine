#!/usr/bin/env python3
"""Create an explicit NOT_RUN record only when an acceptance component produced no JSON."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def commit_sha() -> str:
    if os.environ.get("GITHUB_SHA"):
        return str(os.environ["GITHUB_SHA"])
    completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--component", required=True)
    parser.add_argument("--step-outcome", default="unknown")
    parser.add_argument("--upstream-reason", default="")
    args = parser.parse_args()
    if args.path.is_file():
        try:
            payload = json.loads(args.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            payload = {
                "schema_version": 1,
                "component": args.component,
                "commit_sha": commit_sha(),
                "status": "blocked",
                "result": "INVALID_EVIDENCE",
                "reason": f"Existing report is not valid JSON: {type(exc).__name__}: {exc}",
            }
            atomic_json(args.path, payload)
            return 0
        if isinstance(payload, dict):
            return 0
    atomic_json(args.path, {
        "schema_version": 1,
        "component": args.component,
        "commit_sha": commit_sha(),
        "recorded_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "status": "not_run",
        "result": "NOT_RUN",
        "step_outcome": args.step_outcome,
        "reason": args.upstream_reason or "The component did not produce its mandatory machine report.",
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
