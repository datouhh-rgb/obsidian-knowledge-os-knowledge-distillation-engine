from __future__ import annotations

import os
from pathlib import Path

from knowledge_engine.exposure import SkillExposureManager
from knowledge_engine.utils import parse_frontmatter, sha256_file

from conftest import approve_skill


def test_exposure_create_discover_remove_recreate(distilled, tmp_path: Path) -> None:
    """Runs unchanged on Windows, Linux, and macOS CI workers."""
    vault = distilled["vault"]
    skill = distilled["skill"]
    approve_skill(vault, skill)
    repo = tmp_path / "跨平台 repo with spaces"
    repo.mkdir()
    manager = SkillExposureManager(vault)
    canonical = vault / "05_Knowledge/Skills" / skill / "SKILL.md"
    canonical_hash = sha256_file(canonical)

    first = manager.expose(skill, "repo", repo)
    link = Path(first.link_path or "")
    discovered = link / "SKILL.md"
    assert os.path.lexists(str(link))
    assert discovered.is_file()
    frontmatter, _ = parse_frontmatter(discovered.read_text(encoding="utf-8"))
    assert frontmatter["name"] == skill
    assert manager.status(skill).status == "healthy"

    removed = manager.unexpose(skill)
    assert removed.status == "unexposed"
    assert not os.path.lexists(str(link))
    assert canonical.is_file()
    assert sha256_file(canonical) == canonical_hash

    second = manager.expose(skill, "repo", repo)
    recreated = Path(second.link_path or "")
    assert os.path.lexists(str(recreated))
    assert (recreated / "SKILL.md").is_file()
    assert recreated.resolve() == canonical.parent.resolve()
    assert sha256_file(canonical) == canonical_hash

    manager.unexpose(skill)
    assert not os.path.lexists(str(recreated))
    assert sha256_file(canonical) == canonical_hash
