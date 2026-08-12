from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from knowledge_engine.exposure import SkillExposureManager, remove_exposure_link
from knowledge_engine.transaction import VaultTransaction
from knowledge_engine.utils import sha256_file

from conftest import approve_skill


def test_candidate_cannot_be_exposed(distilled, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="approved"):
        SkillExposureManager(distilled["vault"]).expose(distilled["skill"], "repo", tmp_path)


def test_expose_discover_status_unexpose_preserves_vault(distilled, tmp_path: Path) -> None:
    vault = distilled["vault"]
    skill = distilled["skill"]
    approve_skill(vault, skill)
    repo = tmp_path / "repo"
    repo.mkdir()
    manager = SkillExposureManager(vault, user_agents_root=tmp_path / "user-skills")
    source_skill = vault / "05_Knowledge/Skills" / skill / "SKILL.md"
    before = sha256_file(source_skill)
    exposed = manager.expose(skill, "repo", repo)
    link = Path(exposed.link_path or "")
    assert VaultTransaction._is_link_like(link)
    assert link.resolve() == source_skill.parent.resolve()
    assert manager.status(skill).status == "healthy"
    policy = (source_skill.parent / "agents/openai.yaml").read_text(encoding="utf-8")
    assert "allow_implicit_invocation: false" in policy
    result = manager.unexpose(skill)
    assert result.status == "unexposed"
    assert not os.path.lexists(link)
    assert sha256_file(source_skill) == before


def test_broken_wrong_target_and_scope_conflict_are_detected(distilled, tmp_path: Path) -> None:
    vault = distilled["vault"]
    skill = distilled["skill"]
    approve_skill(vault, skill)
    repo = tmp_path / "repo"; repo.mkdir()
    manager = SkillExposureManager(vault, user_agents_root=tmp_path / "user-skills")
    exposed = manager.expose(skill, "repo", repo)
    with pytest.raises(ValueError, match="重复暴露"):
        manager.expose(skill, "user")
    link = Path(exposed.link_path or "")
    remove_exposure_link(link)
    assert manager.status(skill).issues[0]["code"] == "broken-link"
    manager.reconcile(repair=True)
    other = tmp_path / "other"; other.mkdir()
    VaultTransaction._create_directory_link(link, other)
    with pytest.raises(ValueError, match="冲突|错误"):
        manager.expose(skill, "repo", repo)
    remove_exposure_link(link)


def test_manual_entity_at_exposure_path_is_never_deleted(distilled, tmp_path: Path) -> None:
    vault = distilled["vault"]
    skill = distilled["skill"]
    approve_skill(vault, skill)
    repo = tmp_path / "repo"; repo.mkdir()
    conflict = repo / ".agents/skills" / skill
    conflict.mkdir(parents=True)
    with pytest.raises(ValueError, match="冲突"):
        SkillExposureManager(vault).expose(skill, "repo", repo)
    assert conflict.is_dir()


def test_trigger_harness_has_required_matrix(distilled) -> None:
    from knowledge_engine.trigger_eval import TriggerEvalService
    import json

    path = TriggerEvalService(distilled["vault"]).generate_cases(distilled["skill"])
    result = json.loads(path.read_text(encoding="utf-8"))
    assert sum(item["category"] == "positive" for item in result["cases"]) == 10
    assert sum(item["category"] == "negative" for item in result["cases"]) == 10
    assert sum(item["category"] == "boundary" for item in result["cases"]) == 5
    assert all("observed" not in item for item in result["cases"])


def test_generated_skill_passes_official_skill_creator_validator(distilled) -> None:
    skill_dir = distilled["vault"] / "05_Knowledge/Skills" / distilled["skill"]
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    project_validator = Path(__file__).parents[1] / "scripts/quick_validate_skill.py"
    validators = [project_validator]
    installed_validator = Path.home() / ".codex/skills/.system/skill-creator/scripts/quick_validate.py"
    if installed_validator.is_file():
        validators.append(installed_validator)
    for validator in validators:
        completed = subprocess.run([os.environ.get("PYTHON", "python"), str(validator), str(skill_dir)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env=environment)
        assert completed.returncode == 0, f"{validator}:\n" + completed.stdout + completed.stderr


def test_description_contains_do_when_and_do_not(distilled) -> None:
    text = (distilled["vault"] / "05_Knowledge/Skills" / distilled["skill"] / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    assert "Apply " in frontmatter
    assert "Use when" in frontmatter
    assert "Do not use" in frontmatter
