#!/usr/bin/env python3
"""Build exact-byte release and Git-source manifests for V3.3.0."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAMES = {
    "MANIFEST.sha256",
    "GIT_SOURCE_MANIFEST.sha256",
    "ZIP_SHA256.txt",
}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def staging_files(root: Path) -> list[Path]:
    root = root.resolve()
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = set(relative.parts)
        if ".git" in parts or "__pycache__" in parts or ".pytest_cache" in parts:
            continue
        if relative.name in MANIFEST_NAMES or relative.suffix.casefold() == ".zip":
            continue
        files.append(relative)
    return sorted(files, key=lambda item: item.as_posix())


def render_staging_manifest(root: Path, files: Iterable[Path] | None = None) -> bytes:
    selected = list(files) if files is not None else staging_files(root)
    lines = [f"{sha256(root / path)}  {path.as_posix()}" for path in selected]
    return ("\n".join(lines) + "\n").encode("utf-8")


def git_source_entries(repo: Path, commit: str = "HEAD") -> list[tuple[str, bytes]]:
    completed = subprocess.run(
        ["git", "ls-tree", "-r", "--full-tree", "-z", commit],
        cwd=repo, capture_output=True, check=True,
    )
    entries: list[tuple[str, bytes]] = []
    for record in completed.stdout.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split()
        if kind != "blob" or mode == "160000":
            continue
        path = raw_path.decode("utf-8")
        if Path(path).name in MANIFEST_NAMES or Path(path).suffix.casefold() == ".zip":
            continue
        blob = subprocess.run(
            ["git", "cat-file", "blob", object_id], cwd=repo,
            capture_output=True, check=True,
        ).stdout
        entries.append((path, blob))
    return sorted(entries, key=lambda item: item[0])


def render_git_source_manifest(repo: Path, commit: str = "HEAD") -> bytes:
    lines = [f"{sha256_bytes(payload)}  {path}" for path, payload in git_source_entries(repo, commit)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def parse_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, relative = line.split("  ", 1)
        entries[relative] = digest
    return entries


def verify_staging_manifest(root: Path, manifest: Path) -> list[str]:
    entries = parse_manifest(manifest)
    failures: list[str] = []
    for relative, expected in entries.items():
        path = root / Path(relative)
        if not path.is_file() or sha256(path) != expected:
            failures.append(relative)
    expected_paths = set(entries)
    actual_paths = {path.as_posix() for path in staging_files(root)}
    failures.extend(f"unlisted:{path}" for path in sorted(actual_paths - expected_paths))
    return sorted(failures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--git-source", action="store_true")
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    root = args.staging_root.resolve()
    default_name = "GIT_SOURCE_MANIFEST.sha256" if args.git_source else "MANIFEST.sha256"
    output = (args.output or (root / "release" / default_name)).resolve()
    expected = (
        render_git_source_manifest(ROOT, args.commit)
        if args.git_source else render_staging_manifest(root)
    )
    if args.check:
        passed = output.is_file() and output.read_bytes() == expected
        print(f"manifest_check={'passed' if passed else 'failed'} output={output}")
        return 0 if passed else 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(expected)
    if not args.git_source:
        failures = verify_staging_manifest(root, output)
        print(f"manifest_files={len(parse_manifest(output))} failures={len(failures)}")
        return 0 if not failures else 1
    print(f"git_source_manifest_entries={len(expected.splitlines())} commit={args.commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
