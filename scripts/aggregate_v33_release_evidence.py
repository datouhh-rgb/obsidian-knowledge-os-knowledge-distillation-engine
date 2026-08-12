#!/usr/bin/env python3
"""Aggregate portable CI evidence and generate its single Markdown view."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    ("windows-latest", "3.11"), ("windows-latest", "3.12"),
    ("ubuntu-latest", "3.11"), ("ubuntu-latest", "3.12"),
    ("macos-latest", "3.11"), ("macos-latest", "3.12"),
}


def portable_jobs_valid(jobs: list[dict[str, object]], commit: str) -> bool:
    found = {(str(item.get("os")), str(item.get("python"))) for item in jobs}
    return (
        len(jobs) == 6 and found == EXPECTED
        and all(item.get("job_status") == "passed" for item in jobs)
        and all(item.get("commit_sha") == commit for item in jobs)
    )


def render_ci_markdown(payload: dict[str, object]) -> str:
    jobs = payload.get("jobs", [])
    if not isinstance(jobs, list):
        jobs = []
    valid = payload.get("status") == "passed"
    rows = [
        "# V3.3.0 Portable Core CI Report", "",
        f"- Commit: `{payload.get('commit_sha', '')}`",
        f"- Workflow: {payload.get('workflow_url', '')}",
        f"- Status: `{'PASS' if valid else 'BLOCKED'}`", "",
        "| OS | Python | Pytest | Schema | Manifest | Production |",
        "|---|---:|---|---|---|---|",
    ]
    rows.extend(
        f"| {item['os']} | {item['python']} | {item['pytest_status']} | "
        f"{item['schema_drift_status']} | {item['manifest_staging_status']} | {item['production_check_status']} |"
        for item in sorted(jobs, key=lambda value: (value["os"], value["python"]))
        if isinstance(item, dict)
    )
    return "\n".join(rows) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portable-root", type=Path, required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    paths = sorted(args.portable_root.rglob("job.json"))
    jobs = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    valid = portable_jobs_valid(jobs, args.commit)
    payload = {
        "schema_version": 2,
        "layer": "portable-core",
        "commit_sha": args.commit,
        "workflow_url": args.run_url,
        "status": "passed" if valid else "blocked",
        "jobs": jobs,
    }
    output = ROOT / "release/evidence/ci/cross_platform_matrix.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (ROOT / "release/CI_REPORT.md").write_text(render_ci_markdown(payload), encoding="utf-8", newline="\n")
    print("portable_matrix=passed" if valid else "portable_matrix=blocked")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
