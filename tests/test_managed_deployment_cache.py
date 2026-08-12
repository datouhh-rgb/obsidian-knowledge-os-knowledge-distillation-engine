from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from knowledge_engine.exposure import SkillExposureManager
from knowledge_engine.registry import SkillRegistry
from knowledge_engine.utils import sha256_file

from conftest import approve_skill


def _force_managed_cache(manager: SkillExposureManager, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager, "_preferred_deployment_mode", lambda _link: "managed-cache")


def _expose_managed(distilled, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    vault = distilled["vault"]
    skill = distilled["skill"]
    approve_skill(vault, skill)
    repo = tmp_path / "repo"
    repo.mkdir()
    manager = SkillExposureManager(vault)
    _force_managed_cache(manager, monkeypatch)
    result = manager.expose(skill, "repo", repo)
    return vault, skill, manager, Path(result.link_path or "")


def test_true_symlink_discovery_when_supported(distilled, tmp_path: Path) -> None:
    vault = distilled["vault"]
    skill = distilled["skill"]
    approve_skill(vault, skill)
    repo = tmp_path / "repo"
    repo.mkdir()
    manager = SkillExposureManager(vault)
    link = repo / ".agents/skills" / skill
    if manager._preferred_deployment_mode(link) != "symlink":
        pytest.skip("This host cannot create a true directory symlink without elevation.")
    exposed = manager.expose(skill, "repo", repo)
    assert exposed.deployment_mode == "symlink"
    assert link.is_symlink()
    assert (link / "SKILL.md").read_bytes() == (
        vault / "05_Knowledge/Skills" / skill / "SKILL.md"
    ).read_bytes()
    assert manager.status(skill).status == "healthy"
    manager.unexpose(skill)


def test_deployment_manifest_and_hash(distilled, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, skill, manager, deployment = _expose_managed(distilled, tmp_path, monkeypatch)
    manifest = json.loads((deployment / "DEPLOYMENT_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["authoritative_vault"] == str(vault.resolve())
    assert manifest["source_skill_sha256"] == sha256_file(deployment / "SKILL.md")
    assert manifest["deployed_skill_sha256"] == sha256_file(deployment / "SKILL.md")
    assert manifest["source_artifacts"] == manifest["deployed_artifacts"]
    manager.unexpose(skill)


def test_deployment_copy_is_read_only(distilled, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _vault, skill, manager, deployment = _expose_managed(distilled, tmp_path, monkeypatch)
    writable = [
        path for path in deployment.rglob("*")
        if path.is_file() and path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    ]
    assert not writable
    manager.unexpose(skill)


def test_deployment_drift_blocks_reconcile(distilled, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _vault, skill, manager, deployment = _expose_managed(distilled, tmp_path, monkeypatch)
    manager._make_tree_writable(deployment)
    (deployment / "SKILL.md").write_text("untrusted deployment drift", encoding="utf-8")
    assert manager.status(skill).status == "invalid"
    audit = manager.reconcile(repair=False)
    assert audit["status"] == "issues"
    with pytest.raises(ValueError, match="drift"):
        manager.unexpose(skill)
    assert manager.reconcile(repair=True)["status"] == "passed"
    manager.unexpose(skill)


def test_unexpose_removes_only_derived_cache(distilled, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, skill, manager, deployment = _expose_managed(distilled, tmp_path, monkeypatch)
    source = vault / "05_Knowledge/Skills" / skill / "SKILL.md"
    source_hash = sha256_file(source)
    manager.unexpose(skill)
    assert not os.path.lexists(str(deployment))
    assert source.is_file() and sha256_file(source) == source_hash


def test_managed_deployment_cache_preserves_single_authority(
    distilled, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = distilled["vault"]
    skill = distilled["skill"]
    approve_skill(vault, skill)
    repo = tmp_path / "repo"
    repo.mkdir()
    manager = SkillExposureManager(vault)
    _force_managed_cache(manager, monkeypatch)

    result = manager.expose(skill, "repo", repo)
    deployment = Path(result.link_path or "")
    registry = SkillRegistry.load(vault)
    record, version_record, lifecycle = registry.version(skill)
    source_version = (vault / version_record["artifact_path"]).parent

    assert result.deployment_mode == "managed-cache"
    assert lifecycle["status"] == "approved"
    assert deployment.is_dir() and not deployment.is_symlink()
    assert (deployment / "SKILL.md").is_file()
    assert sha256_file(deployment / "SKILL.md") == version_record["artifact_sha256"]
    manifest = json.loads((deployment / "DEPLOYMENT_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["authoritative_vault"] == str(vault.resolve())
    assert Path(manifest["source_version_path"]).resolve() == source_version.resolve()
    assert manifest["version"] == record["current_version"]
    assert manifest["reverse_sync"] is False
    assert manifest["read_only"] is True
    assert manager.status(skill).status == "healthy"

    vault_hash = sha256_file(vault / "05_Knowledge/Skills" / skill / "SKILL.md")
    manager._make_tree_writable(deployment)
    (deployment / "SKILL.md").write_text("tampered cache", encoding="utf-8")
    assert manager.status(skill).status == "invalid"
    assert sha256_file(vault / "05_Knowledge/Skills" / skill / "SKILL.md") == vault_hash
    with pytest.raises(ValueError, match="drift"):
        manager.unexpose(skill)

    repaired = manager.reconcile(repair=True)
    assert repaired["status"] == "passed"
    assert any(item["action"] == "rebuild-managed-cache" for item in repaired["repair_actions"])
    assert repaired["event_id"]
    assert manager.status(skill).status == "healthy"

    removed = manager.unexpose(skill)
    assert removed.status == "unexposed"
    assert not os.path.lexists(str(deployment))
    assert sha256_file(vault / "05_Knowledge/Skills" / skill / "SKILL.md") == vault_hash


def test_managed_cache_reuses_only_identical_owned_deployment(
    distilled, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = distilled["vault"]
    skill = distilled["skill"]
    approve_skill(vault, skill)
    repo = tmp_path / "repo"
    repo.mkdir()
    manager = SkillExposureManager(vault)
    _force_managed_cache(manager, monkeypatch)

    first = manager.expose(skill, "repo", repo, idempotency_key="same-request")
    second = manager.expose(skill, "repo", repo, idempotency_key="same-request")
    deployment = Path(first.link_path or "")

    assert first.status == "exposed"
    assert second.status == "duplicate"
    assert deployment.is_dir()
    assert manager.status(skill).status == "healthy"

    manager.unexpose(skill)
    assert not os.path.lexists(str(deployment))
