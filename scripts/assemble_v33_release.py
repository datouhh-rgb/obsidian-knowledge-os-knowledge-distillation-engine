#!/usr/bin/env python3
"""Assemble the one V3.3.0 release archive from an immutable Git commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from scripts.build_v33_release_manifest import render_staging_manifest, verify_staging_manifest


ROOT = Path(__file__).resolve().parents[1]
READY_NAME = "Obsidian-Knowledge-OS-Knowledge-Distillation-Engine-v3.3.0-Production-Ready.zip"
BLOCKED_NAME = "Obsidian-Knowledge-OS-Knowledge-Distillation-Engine-v3.3.0-BLOCKED-Audit.zip"


def release_origin(commit: str, workflow_url: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "engine_version": "3.3.0",
        "commit_sha": commit,
        "workflow_url": workflow_url,
        "assembly": "git-archive-plus-canonical-release-evidence",
        "assembly_job": "release-assembly",
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_current_release(stage: Path) -> None:
    target = stage / "release"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name in (
        "FINAL_ACCEPTANCE_REPORT.md", "CI_REPORT.md", "REAL_CODEX_EXPLICIT_ONLY_REPORT.md",
        "BOOK_REGRESSION_REPORT.md", "CONVERSION_QUALITY_REPORT.md", "SKILL_SEMANTIC_QUALITY_REPORT.md",
    ):
        shutil.copy2(ROOT / "release" / name, target / name)
    for relative in (
        Path("evidence/ci/cross_platform_matrix.json"),
        Path("evidence/codex_real_test"),
        Path("evidence/regressions/stock_operator_v33_canonical.json"),
        Path("evidence/regressions/cognitive_awakening_v33_canonical.json"),
    ):
        source = ROOT / "release" / relative
        destination = target / relative
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    example = ROOT / "release/SKILL_EXAMPLE"
    if example.is_dir():
        shutil.copytree(example, target / "SKILL_EXAMPLE")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--workflow-url", required=True)
    parser.add_argument("--blocked", action="store_true")
    args = parser.parse_args()
    actual = subprocess.run(
        ["git", "rev-parse", args.commit], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="v33-release-assembly-") as temporary:
        temp = Path(temporary)
        base = temp / "git-source.zip"
        stage = temp / "stage"
        subprocess.run(["git", "archive", "--format=zip", "-o", str(base), actual], cwd=ROOT, check=True)
        with zipfile.ZipFile(base) as handle:
            handle.extractall(stage)
        copy_current_release(stage)
        root_report = ROOT / "FINAL_PRODUCTION_ACCEPTANCE_REPORT.md"
        if root_report.is_file():
            shutil.copy2(root_report, stage / root_report.name)
        origin = release_origin(actual, args.workflow_url)
        (stage / "release/RELEASE_ORIGIN.json").write_text(
            json.dumps(origin, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n",
        )
        # A ZIP cannot contain its own final digest without changing that
        # digest. Keep an explicit in-archive detached-hash policy document;
        # the adjacent release/ZIP_SHA256.txt artifact is replaced below with
        # the actual digest after the immutable ZIP bytes exist.
        (stage / "release/ZIP_SHA256.txt").write_text(
            "DETACHED_SHA256\n"
            "The final archive SHA-256 is published as the adjacent release/ZIP_SHA256.txt artifact "
            "from the same release-assembly job.\n",
            encoding="utf-8", newline="\n",
        )
        manifest = stage / "release/MANIFEST.sha256"
        manifest.write_bytes(render_staging_manifest(stage))
        if verify_staging_manifest(stage, manifest):
            raise RuntimeError("Staging manifest did not verify before ZIP creation.")
        (ROOT / "release/MANIFEST.sha256").write_bytes(manifest.read_bytes())
        output = ROOT / (BLOCKED_NAME if args.blocked else READY_NAME)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(stage).as_posix())
        extracted = temp / "extracted"
        with zipfile.ZipFile(output) as archive:
            archive.extractall(extracted)
        failures = verify_staging_manifest(extracted, extracted / "release/MANIFEST.sha256")
        if failures:
            raise RuntimeError("Archive exact-byte manifest failed: " + ", ".join(failures[:20]))
        zip_hash = sha256(output)
        (ROOT / "release/ZIP_SHA256.txt").write_text(
            f"{zip_hash}  {output.name}\n", encoding="utf-8", newline="\n",
        )
        print(f"archive={output}")
        print(f"sha256={zip_hash}")
        print("manifest_exact_bytes=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
