from __future__ import annotations

from knowledge_engine.evidence import EvidenceCatalog
from knowledge_engine.structure import RealStructureNormalizer


def test_frontmatter_boundary_preserves_provenance_not_knowledge() -> None:
    raw = """---
title: "股票大作手回忆录"
source_path: C:\\Books\\stock.azw3
source_sha256: abcdef
converter: KindleUnpack
kindleunpack_version: 0.83
kindleunpack_commit: deadbeef
---

# 第一章

价格按趋势运行时，应记录市场事实。
"""
    normalized = RealStructureNormalizer().normalize(raw, "markdown").text
    assert "converter:" not in normalized
    assert "source_path:" not in normalized
    catalog = EvidenceCatalog.from_text("股票大作手回忆录", "source", normalized, "conversion")
    excerpts = "\n".join(item.excerpt for item in catalog.items.values()).casefold()
    for leaked in ("converter", "kindleunpack", "source_path", "source_sha256"):
        assert leaked not in excerpts
    assert "价格按趋势运行" in excerpts


def test_sourcepath_sourcesha_converter_reserved_terms() -> None:
    raw = """{
  "converter": "KindleUnpack",
  "kindleunpackcommit": "deadbeef",
  "sourcepath": "C:/Books/stock.azw3",
  "sourcesha256": "abcdef"
}

# 正文

核验市场信号并记录实际结果。
"""
    normalized = RealStructureNormalizer().normalize(raw, "markdown").text
    assert "kindleunpackcommit" not in normalized.casefold()
    catalog = EvidenceCatalog.from_text("股票大作手回忆录", "source", normalized, "conversion")
    assert all("sourcepath" not in item.excerpt.casefold() for item in catalog.items.values())
    assert any("市场信号" in item.excerpt for item in catalog.items.values())


def test_conversion_metadata_not_promoted_to_concept() -> None:
    text = """# 股票大作手回忆录

title: 股票大作手回忆录

ISBN 978-7-5506-1123-8

标准书号 ISBN 978-7-5506-1123-8

# 第一章

比较价格反馈与既定计划，并记录偏差。
"""
    catalog = EvidenceCatalog.from_text("股票大作手回忆录", "source", text, "conversion")
    excerpts = [item.excerpt for item in catalog.items.values()]
    assert not any("ISBN" in item or item.startswith("title:") for item in excerpts)
    assert any("价格反馈" in item for item in excerpts)
