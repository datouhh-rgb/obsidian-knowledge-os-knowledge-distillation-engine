from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

from scripts.build_v33_release_manifest import (
    render_git_source_manifest,
    render_staging_manifest,
    sha256,
    verify_staging_manifest,
)
from scripts.export_schemas import ROOT, rendered_schemas


def test_export_schemas_produces_clean_git_diff() -> None:
    assert rendered_schemas() == rendered_schemas()
    assert all(content.endswith(b"\n") and b"\r\n" not in content for content in rendered_schemas().values())


def test_no_stale_schema_in_release() -> None:
    for name, expected in rendered_schemas().items():
        assert (ROOT / "schemas" / name).read_bytes() == expected


def test_release_manifest_exact_bytes(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    (stage / "nested").mkdir(parents=True)
    (stage / "lf.txt").write_bytes(b"one\ntwo\n")
    (stage / "nested" / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    (stage / "binary.bin").write_bytes(bytes(range(32)))
    manifest = stage / "release" / "MANIFEST.sha256"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(render_staging_manifest(stage))
    assert not verify_staging_manifest(stage, manifest)
    assert sha256(stage / "lf.txt") != sha256(stage / "nested" / "crlf.txt")


def test_release_manifest_cross_platform_extraction(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    (stage / "release").mkdir(parents=True)
    (stage / "payload.txt").write_bytes(b"exact\r\nbytes\n")
    manifest = stage / "release" / "MANIFEST.sha256"
    manifest.write_bytes(render_staging_manifest(stage))
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(stage).as_posix())
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(archive) as handle:
        handle.extractall(extracted)
    assert not verify_staging_manifest(extracted, extracted / "release" / "MANIFEST.sha256")
    assert (extracted / "payload.txt").read_bytes() == b"exact\r\nbytes\n"


def test_schema_manifest_matches_archive(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "audit@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Audit"], cwd=repo, check=True)
    (repo / "tracked.txt").write_bytes(b"tracked\r\nbytes\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)
    first = render_git_source_manifest(repo, "HEAD")
    (repo / "tracked.txt").write_bytes(b"dirty working tree")
    second = render_git_source_manifest(repo, "HEAD")
    assert first == second
    assert b"tracked.txt" in first
