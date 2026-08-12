#!/usr/bin/env python3
"""Verify the exact final V3.3 archive after extraction on the current OS."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path
import tempfile
import zipfile

from scripts.build_v33_release_manifest import verify_staging_manifest


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(archive: Path, expected_commit: str, workflow_url: str) -> dict[str, object]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="v33-archive-verify-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(archive) as handle:
            unsafe = [
                name for name in handle.namelist()
                if Path(name).is_absolute() or ".." in Path(name).parts
            ]
            if unsafe:
                failures.extend(f"unsafe archive member: {name}" for name in unsafe)
            else:
                handle.extractall(root)
        manifest = root / "release/MANIFEST.sha256"
        if not manifest.is_file():
            failures.append("release/MANIFEST.sha256 missing")
        else:
            failures.extend(verify_staging_manifest(root, manifest))
        zip_hash_policy = root / "release/ZIP_SHA256.txt"
        if not zip_hash_policy.is_file() or not zip_hash_policy.read_text(encoding="utf-8").startswith("DETACHED_SHA256\n"):
            failures.append("release/ZIP_SHA256.txt detached-hash policy missing")
        origin_path = root / "release/RELEASE_ORIGIN.json"
        origin = json.loads(origin_path.read_text(encoding="utf-8")) if origin_path.is_file() else {}
        if origin.get("commit_sha") != expected_commit:
            failures.append("release origin commit mismatch")
        if origin.get("assembly_job") != "release-assembly":
            failures.append("release origin assembly job mismatch")
        if origin.get("workflow_url") != workflow_url:
            failures.append("release origin workflow URL mismatch")
    return {
        "schema_version": 1,
        "os": platform.system(),
        "platform": platform.platform(),
        "commit_sha": expected_commit,
        "workflow_url": workflow_url,
        "archive_name": archive.name,
        "archive_sha256": sha256(archive),
        "manifest_exact_bytes": not failures,
        "failures": failures,
        "status": "passed" if not failures else "blocked",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--workflow-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = verify_archive(args.archive.resolve(), args.expected_commit, args.workflow_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"archive_manifest={payload['status']}")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
