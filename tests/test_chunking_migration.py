from __future__ import annotations

import json
from pathlib import Path

from knowledge_engine.chunking import ChapterChunker, ResumableChunkRunner
from knowledge_engine.integrity import IntegrityReconciler
from knowledge_engine.migration import V2MigrationService
from knowledge_engine.registry import SkillRegistry
from knowledge_engine.utils import sha256_file


def test_long_document_chunk_cache_resume_and_affected_only(tmp_path: Path) -> None:
    text = "# Long\n\n" + "\n\n".join(f"## Chapter {i}\n\n" + (f"content-{i} " * 500) for i in range(12))
    chunker = ChapterChunker(max_chars=2000, overlap=100)
    chunks = chunker.split(text, "c" * 64)
    assert len(chunks) > 12
    assert all(item.chunk_id.startswith("chunk-") for item in chunks)
    runner = ResumableChunkRunner(tmp_path / "state")
    extractor = lambda chunk: {"hash": chunk.text_sha256}
    interrupted = runner.run(chunks, "c" * 64, "rule", "none", {}, "p1", "s1", extractor, fail_after=3)
    assert interrupted.status == "interrupted"
    manifest = json.loads((tmp_path / "state" / interrupted.run_id / "RUN.json").read_text(encoding="utf-8"))
    assert manifest["status"] != "completed"
    resumed = runner.run(chunks, "c" * 64, "rule", "none", {}, "p1", "s1", extractor)
    assert resumed.status == "completed"
    assert len(resumed.cache_hits) == 3
    assert len(resumed.processed) == len(chunks) - 3

    changed_text = text.replace("content-5", "CONTENT-5")
    changed = chunker.split(changed_text, "c" * 64)
    rerun = runner.run(changed, "c" * 64, "rule", "none", {}, "p1", "s1", extractor)
    assert rerun.status == "completed"
    assert 0 < len(rerun.processed) < len(changed)
    assert len(rerun.cache_hits) + len(rerun.processed) == len(changed)


def _make_v2_fixture(vault: Path) -> None:
    source_one = vault / "06_Sources/Books/SameBook"
    source_two = vault / "06_Sources/Books/SameBook--abcdef12"
    source_one.mkdir(parents=True); source_two.mkdir(parents=True)
    (source_one / "SOURCE_TEXT.md").write_text("# one\n", encoding="utf-8")
    (source_two / "SOURCE_TEXT.md").write_text("# two\n", encoding="utf-8")
    (source_one / "EVIDENCE_CATALOG.json").write_text(json.dumps({"items": [{"evidence_id": "e-old", "excerpt": "one"}]}), encoding="utf-8")
    skill_root = vault / "05_Knowledge/Skills/old-skill"
    (skill_root / "agents").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("---\nname: old-skill\ndescription: old\n---\n# Old\n", encoding="utf-8")
    (skill_root / "agents/openai.yaml").write_text("policy:\n  allow_implicit_invocation: false\n", encoding="utf-8")
    registry = {
        "schema_version": 2, "engine_version": "2.0.0", "skills": {
            "old-skill": {
                "title": "Same Method", "version": "1.1.0", "status": "active",
                "skill_path": "05_Knowledge/Skills/old-skill/SKILL.md", "semantic_text": "old",
                "source_books": [
                    {"book_title": "SameBook", "sha256": "a" * 64, "evidence_ids": ["e-old"]},
                    {"book_title": "SameBook", "sha256": "b" * 64, "evidence_ids": ["e-old"]},
                ],
                "history": [{"version": "1.0.0"}],
            }
        },
    }
    (vault / "05_Knowledge/Skills/REGISTRY.json").write_text(json.dumps(registry), encoding="utf-8")


def test_v2_migration_dry_run_apply_rollback_and_idempotency(vault: Path) -> None:
    _make_v2_fixture(vault)
    old_files = [path for path in vault.rglob("*") if path.is_file() and "REGISTRY_V3" not in path.name]
    before = {path.relative_to(vault).as_posix(): sha256_file(path) for path in old_files}
    service = V2MigrationService(vault)
    dry = service.dry_run()
    assert dry.plan["duplicate_source_directories"]
    assert dry.plan["overwrite_style_skills"]
    assert dry.plan["evidence_rebinding_risks"]
    assert dry.plan["status_inconsistencies"]
    applied = service.apply()
    assert applied.status == "applied"
    assert service.apply().status == "idempotent"
    after_old = {relative: sha256_file(vault / relative) for relative in before}
    assert after_old == before
    registry = SkillRegistry.load(vault)
    _, _, lifecycle = registry.version(next(iter(registry.by_name)), "0.0.0")
    assert lifecycle["status"] == "legacy-review"
    assert lifecycle["usage"]["invocation_count"] == 0
    assert IntegrityReconciler(vault).reconcile().passed
    rolled = service.rollback(applied.migration_id)
    assert rolled.status == "rolled-back"
    assert service.rollback(applied.migration_id).status == "idempotent-rollback"
    assert {relative: sha256_file(vault / relative) for relative in before} == before
