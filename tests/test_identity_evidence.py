from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_engine.evidence import EvidenceCatalog, evidence_id_for
from knowledge_engine.identity import conversion_identity
from knowledge_engine.integrity import IntegrityReconciler
from knowledge_engine.models import IngestedSource
from knowledge_engine.pipeline import DistillationPipeline
from knowledge_engine.registry import SkillRegistry
from knowledge_engine.utils import sha256_file
from conftest import make_production_pipeline


def test_same_title_sources_and_editions_coexist(vault: Path, source_factory) -> None:
    first = source_factory(suffix="第一版附录")
    second = source_factory(
        suffix="第二版附录", method_title="第二版差异复核流程",
        steps=["收集第二版目标。", "比较第二版差异。", "记录第二版决策。", "输出第二版报告。"],
    )
    one = make_production_pipeline(vault).distill(str(first), title="同名书", edition_id="edition-1")
    two = make_production_pipeline(vault).distill(str(second), title="同名书", edition_id="edition-2")
    assert one.source_id != two.source_id
    assert one.source_root != two.source_root
    assert (one.source_root / "originals").is_dir()
    assert (two.source_root / "originals").is_dir()
    index = json.loads((vault / "06_Sources/Books/SOURCE_INDEX.json").read_text(encoding="utf-8"))
    assert len(index["titles"]["同名书"]) == 2
    assert len(SkillRegistry.load(vault).records) == 2


def test_conversion_identity_changes_with_converter_config(tmp_path: Path) -> None:
    source_path = tmp_path / "same.md"
    source_path.write_text("# A\n\ntext\n", encoding="utf-8")
    source_id = sha256_file(source_path)
    one = IngestedSource(str(source_path), source_path, source_path.name, "text/markdown", "markdown", source_id, source_path.read_text(encoding="utf-8"), {"converter": "identity", "version": "1", "config": {"mode": "a"}})
    two = IngestedSource(str(source_path), source_path, source_path.name, "text/markdown", "markdown", source_id, source_path.read_text(encoding="utf-8"), {"converter": "identity", "version": "1", "config": {"mode": "b"}})
    conversion_one = conversion_identity(one)
    conversion_two = conversion_identity(two)
    assert conversion_one.conversion_id != conversion_two.conversion_id
    catalog_one = EvidenceCatalog.from_text("A", source_id, one.text, conversion_one.conversion_id, str(source_path))
    catalog_two = EvidenceCatalog.from_text("A", source_id, two.text, conversion_two.conversion_id, str(source_path))
    assert catalog_one.verify_integrity().passed
    assert catalog_two.verify_integrity().passed
    assert set(catalog_one.items).isdisjoint(catalog_two.items)


def test_evidence_id_cannot_rebind_excerpt() -> None:
    conversion = "c" * 64
    one = evidence_id_for(conversion, "lines:1-1", "alpha")
    two = evidence_id_for(conversion, "lines:1-1", "beta")
    assert one != two


@pytest.mark.parametrize("target", ["original", "source_text", "evidence", "skill"])
def test_any_byte_tamper_fails_integrity(distilled, target: str) -> None:
    vault = distilled["vault"]
    result = distilled["result"]
    registry = SkillRegistry.load(vault)
    record = registry.require(distilled["skill"])
    version = record["versions"][record["current_version"]]
    conversion_root = next((result.source_root / "conversions").iterdir())
    paths = {
        "original": next((result.source_root / "originals").iterdir()),
        "source_text": conversion_root / "SOURCE_TEXT.md",
        "evidence": conversion_root / "EVIDENCE_CATALOG.json",
        "skill": vault / version["artifact_path"],
    }
    path = paths[target]
    path.write_bytes(path.read_bytes() + b"X")
    report = IntegrityReconciler(vault).reconcile()
    assert not report.passed


def test_evidence_catalog_detects_locator_and_hash_tamper() -> None:
    catalog = EvidenceCatalog.from_text("T", "a" * 64, "# T\n\nalpha beta\n", "b" * 64, "original.md")
    payload = catalog.as_dict()
    payload["items"][0]["excerpt"] = "changed"
    loaded = EvidenceCatalog.from_dict(payload, catalog.source_text)
    assert not loaded.verify_integrity().passed
