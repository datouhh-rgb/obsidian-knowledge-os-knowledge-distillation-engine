#!/usr/bin/env python3
"""Fail closed unless the same final ZIP passed on Windows, Linux, and macOS."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


EXPECTED_OS = {"Windows", "Linux", "Darwin"}


def aggregate(paths: list[Path], commit: str, workflow_url: str) -> dict[str, object]:
    jobs = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    systems = {str(item.get("os")) for item in jobs}
    hashes = {str(item.get("archive_sha256", "")) for item in jobs}
    valid = (
        len(jobs) == 3
        and systems == EXPECTED_OS
        and len(hashes) == 1
        and "" not in hashes
        and all(item.get("status") == "passed" for item in jobs)
        and all(item.get("commit_sha") == commit for item in jobs)
        and all(item.get("workflow_url") == workflow_url for item in jobs)
    )
    return {
        "schema_version": 1,
        "commit_sha": commit,
        "workflow_url": workflow_url,
        "archive_sha256": next(iter(hashes), "") if len(hashes) == 1 else "",
        "status": "passed" if valid else "blocked",
        "jobs": sorted(jobs, key=lambda item: str(item.get("os"))),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--workflow-url", required=True)
    parser.add_argument("--output", type=Path, default=Path("release/evidence/ci/archive_cross_platform.json"))
    args = parser.parse_args()
    payload = aggregate(sorted(args.root.rglob("archive-verification.json")), args.commit, args.workflow_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print("V3.3.0_PRODUCTION_READY" if payload["status"] == "passed" else "V3.3.0_BLOCKED")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
