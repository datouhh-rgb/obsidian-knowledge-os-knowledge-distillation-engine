from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from knowledge_engine.conversion import (
    CANONICAL_CONVERSION_SCHEMA,
    CanonicalConversionError,
    convert_epub_directory,
    decode_markup_member,
)
from knowledge_engine.conversion_quality import ConversionQualityGate
from knowledge_engine.identity import bind_source_identity, conversion_identity
from knowledge_engine.ingest import IngestError, SourceIngester
from knowledge_engine.models import IngestedSource, ProviderResult
from knowledge_engine.pipeline import DistillationPipeline


def _write_epub_directory(root: Path, body: bytes) -> Path:
    (root / "META-INF").mkdir(parents=True)
    (root / "OEBPS/Text").mkdir(parents=True)
    (root / "META-INF/container.xml").write_text(
        "<?xml version='1.0' encoding='utf-8'?><container><rootfiles>"
        "<rootfile full-path='OEBPS/content.opf'/></rootfiles></container>",
        encoding="utf-8",
    )
    (root / "OEBPS/content.opf").write_text(
        "<?xml version='1.0' encoding='utf-8'?><package><manifest>"
        "<item id='chapter' href='Text/chapter.xhtml' media-type='application/xhtml+xml'/>"
        "</manifest><spine><itemref idref='chapter'/></spine></package>",
        encoding="utf-8",
    )
    (root / "OEBPS/Text/chapter.xhtml").write_bytes(body)
    return root


def _fake_kindleunpack(tmp_path: Path) -> Path:
    script = tmp_path / "kindleunpack.py"
    script.write_text(
        """from __future__ import annotations
import hashlib, sys
from pathlib import Path
source, output = Path(sys.argv[-2]), Path(sys.argv[-1])
raw = source.read_bytes()
(output / 'META-INF').mkdir(parents=True, exist_ok=True)
(output / 'OEBPS/Text').mkdir(parents=True, exist_ok=True)
(output / 'META-INF/container.xml').write_text(
    \"<?xml version='1.0' encoding='utf-8'?><container><rootfiles><rootfile full-path='OEBPS/content.opf'/></rootfiles></container>\",
    encoding='utf-8')
(output / 'OEBPS/content.opf').write_text(
    \"<?xml version='1.0' encoding='utf-8'?><package><manifest><item id='c' href='Text/c.xhtml' media-type='application/xhtml+xml'/></manifest><spine><itemref idref='c'/></spine></package>\",
    encoding='utf-8')
digest = hashlib.sha256(raw).hexdigest()
(output / 'OEBPS/Text/c.xhtml').write_text(
    f\"<?xml version='1.0' encoding='utf-8'?><html><body><h1>正文</h1><p>原始输入摘要 {digest}，用于证明转换器读取了本次传入的原始文件。</p></body></html>\",
    encoding='utf-8')
print('KindleUnpack v0.83')
""",
        encoding="utf-8",
    )
    return script


def _ingest_fake_azw3(tmp_path: Path, raw: bytes, variant: str = "A") -> IngestedSource:
    source = tmp_path / f"book-{variant}.azw3"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(raw)
    converter_root = tmp_path / f"converter-{variant}"
    converter_root.mkdir(parents=True, exist_ok=True)
    converter = _fake_kindleunpack(converter_root)
    ingester = SourceIngester(
        tmp_path / f"work-{variant}",
        azw3_command=(sys.executable, str(converter), f"--variant={variant}", "{input}", "{output_dir}"),
    )
    result = ingester.ingest(str(source))
    bind_source_identity(result)
    return result


def test_declared_xml_charset_decode() -> None:
    body = "<?xml version='1.0' encoding='gb18030'?><p>市场趋势判断</p>".encode("gb18030")
    decoded = decode_markup_member(body, "chapter.xhtml")
    assert decoded.encoding == "gb18030"
    assert decoded.strategy == "xml-declaration"
    assert "市场趋势判断" in decoded.text


def test_html_meta_charset_decode() -> None:
    body = "<html><head><meta charset='big5'></head><body>市場趨勢判斷</body></html>".encode("big5")
    decoded = decode_markup_member(body, "chapter.xhtml")
    assert decoded.encoding == "big5"
    assert decoded.strategy == "html-meta-charset"


def test_invalid_utf8_does_not_use_replacement_mode() -> None:
    with pytest.raises(CanonicalConversionError):
        decode_markup_member(b"\x81", "broken.xhtml")


def test_chinese_controlled_fallback_is_audited() -> None:
    decoded = decode_markup_member("市场趋势需要连续观察".encode("gb18030"), "legacy.xhtml")
    assert decoded.encoding == "gb18030"
    assert decoded.strategy == "controlled-fallback-audited"
    assert any("audited" in item for item in decoded.attempts)


def test_undecodable_spine_blocks_conversion(tmp_path: Path) -> None:
    epub = _write_epub_directory(tmp_path / "epub", b"\x81")
    with pytest.raises(CanonicalConversionError):
        convert_epub_directory(epub)


def test_per_spine_encoding_manifest_is_deterministic(tmp_path: Path) -> None:
    body = "<?xml version='1.0' encoding='utf-8'?><html><body><h1>章节</h1><p>市场趋势需要连续观察。</p></body></html>".encode()
    epub = _write_epub_directory(tmp_path / "epub", body)
    first = convert_epub_directory(epub)
    second = convert_epub_directory(epub)
    assert first.text == second.text
    assert first.metadata == second.metadata
    member = first.metadata["spine_members"][0]  # type: ignore[index]
    assert member["detected_encoding"] == "utf-8"  # type: ignore[index]
    assert member["raw_sha256"] and member["decoded_sha256"]  # type: ignore[index]


def test_repacked_epub_byproduct_does_not_change_conversion_identity(tmp_path: Path) -> None:
    source = tmp_path / "book.azw3"
    source.write_bytes(b"stable-original")
    converter = tmp_path / "kindleunpack.py"
    converter.write_text(
        """from __future__ import annotations
import os, sys
from pathlib import Path
source, output = Path(sys.argv[-2]), Path(sys.argv[-1])
(output / 'META-INF').mkdir(parents=True, exist_ok=True)
(output / 'OEBPS/Text').mkdir(parents=True, exist_ok=True)
(output / 'META-INF/container.xml').write_text(
    \"<?xml version='1.0' encoding='utf-8'?><container><rootfiles><rootfile full-path='OEBPS/content.opf'/></rootfiles></container>\",
    encoding='utf-8')
(output / 'OEBPS/content.opf').write_text(
    \"<?xml version='1.0' encoding='utf-8'?><package><manifest><item id='c' href='Text/c.xhtml' media-type='application/xhtml+xml'/></manifest><spine><itemref idref='c'/></spine></package>\",
    encoding='utf-8')
(output / 'OEBPS/Text/c.xhtml').write_text(
    \"<?xml version='1.0' encoding='utf-8'?><html><body><h1>正文</h1><p>市场趋势需要连续观察。</p></body></html>\",
    encoding='utf-8')
(output / 'derived.epub').write_bytes(os.urandom(32))
print('KindleUnpack v0.83')
""",
        encoding="utf-8",
    )

    def convert(label: str) -> IngestedSource:
        result = SourceIngester(
            tmp_path / label,
            azw3_command=(sys.executable, str(converter), "{input}", "{output_dir}"),
        ).ingest(str(source))
        bind_source_identity(result)
        return result

    first = convert("first")
    second = convert("second")
    assert first.text == second.text
    assert conversion_identity(first).conversion_id == conversion_identity(second).conversion_id
    config = first.converter_metadata["config"]
    assert config["container_manifest_policy"] == "canonical-members-exclude-derived-archives-v1"  # type: ignore[index]
    assert config["excluded_derived_members"] == ["derived.epub"]  # type: ignore[index]


def test_converter_input_was_original_source(tmp_path: Path) -> None:
    source = _ingest_fake_azw3(tmp_path, b"original-azw3-bytes")
    config = source.converter_metadata["config"]
    attestation = config["converter_input_attestation"]  # type: ignore[index]
    assert attestation["exact_original_path_supplied"] is True  # type: ignore[index]
    assert attestation["source_sha256_before"] == source.sha256  # type: ignore[index]
    assert attestation["source_sha256_after"] == source.sha256  # type: ignore[index]
    assert source.sha256 in source.text


def test_conversion_cache_bound_to_source_and_converter(tmp_path: Path) -> None:
    source = _ingest_fake_azw3(tmp_path, b"source-one")
    config = source.converter_metadata["config"]
    assert config["cache_policy"] == "disabled-canonical-acceptance"  # type: ignore[index]
    assert config["source_bytes_sha256"] == source.source_id  # type: ignore[index]
    assert config["converter_name"] == "KindleUnpack"  # type: ignore[index]
    assert config["converter_version"] == "0.83"  # type: ignore[index]
    assert config["converter_commit"]  # type: ignore[index]
    assert config["command_fingerprint"]  # type: ignore[index]
    assert config["conversion_schema_version"] == CANONICAL_CONVERSION_SCHEMA  # type: ignore[index]
    assert source.converter_metadata["output_sha256"] == config["normalized_output_sha256"]  # type: ignore[index]


def test_changed_converter_config_invalidates_conversion_cache(tmp_path: Path) -> None:
    first = _ingest_fake_azw3(tmp_path / "first", b"same-source", "A")
    second = _ingest_fake_azw3(tmp_path / "second", b"same-source", "B")
    assert first.source_id == second.source_id
    assert first.text == second.text
    assert first.converter_metadata["config"]["command_fingerprint"] != second.converter_metadata["config"]["command_fingerprint"]  # type: ignore[index]
    assert conversion_identity(first).conversion_id != conversion_identity(second).conversion_id


def test_legacy_single_file_azw3_output_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "book.azw3"
    source.write_bytes(b"book")
    with pytest.raises(IngestError, match="Canonical AZW3 command"):
        SourceIngester(
            tmp_path / "work",
            azw3_command=(sys.executable, "converter.py", "{input}", "{output}"),
        ).ingest(str(source))


def _quality_source(text: str) -> IngestedSource:
    return IngestedSource(
        "book.azw3", Path("book.azw3"), "book.azw3", "application/octet-stream", "azw3", "a" * 64,
        text,
        {
            "converter": "KindleUnpack canonical EPUB spine adapter",
            "version": "test",
            "config": {"spine_members": [{"path": "chapter.xhtml"}], "image_omissions": [], "missing_resources": []},
        },
        identity_source_id="a" * 64,
    )


def test_mojibake_blocks_before_chunking() -> None:
    source = _quality_source("# 章节\n\n" + "市场趋势" * 300 + " 鐨勫 市场")
    result = ConversionQualityGate().validate(source)
    assert not result.passed
    assert any(item.code == "mojibake-pattern" for item in result.issues)


def test_replacement_character_blocks_before_llm() -> None:
    source = _quality_source("# 章节\n\n" + "市场趋势" * 300 + "\ufffd")
    result = ConversionQualityGate().validate(source)
    assert not result.passed
    assert result.metrics["llm_calls"] == 0


def test_conversion_quality_blocks_before_llm() -> None:
    source = _quality_source("# 章节\n\n" + "市场趋势需要连续观察。" * 200 + "\ufffd")
    attestation = ConversionQualityGate().validate(source)
    assert attestation.status == "blocked"
    assert attestation.metrics["llm_calls"] == 0


def test_conversion_quality_attestation_bound_to_conversion_id() -> None:
    source = _quality_source("# 章节\n\n" + "市场趋势需要连续观察。" * 200)
    attestation = ConversionQualityGate().validate(source).bind("b" * 64)
    assert attestation.passed
    assert attestation.conversion_id == "b" * 64
    assert attestation.as_dict()["attestation_sha256"]


def test_conversion_failure_has_no_formal_vault_write(tmp_path: Path, vault: Path) -> None:
    body = (
        "<?xml version='1.0' encoding='utf-8'?><html><body><h1>章节</h1><p>"
        + "市场趋势需要连续观察。" * 200 + "\ufffd</p></body></html>"
    ).encode("utf-8")
    epub_dir = _write_epub_directory(tmp_path / "epub", body)
    epub_path = tmp_path / "broken.epub"
    import zipfile
    with zipfile.ZipFile(epub_path, "w") as archive:
        for item in epub_dir.rglob("*"):
            if item.is_file():
                archive.write(item, item.relative_to(epub_dir).as_posix())

    class CountingProvider:
        name = "llm:must-not-run"

        def __init__(self) -> None:
            self.calls = 0

        def propose(self, analysis, catalog):  # type: ignore[no-untyped-def]
            self.calls += 1
            return ProviderResult(provider=self.name)

    provider = CountingProvider()
    result = DistillationPipeline(vault, provider=provider).distill(str(epub_path), title="损坏转换")
    assert result.status == "conversion-quality-blocked"
    assert provider.calls == 0
    assert result.written_paths == []
    assert not (vault / "05_Knowledge/Graph/concept-graph.v3.json").exists()
    assert not (vault / "05_Knowledge/Skills/REGISTRY_V3.json").exists()
    assert not (vault / "05_Knowledge/Audit/ASSET_LEDGER.json").exists()
    diagnostics = list((vault / ".knowledge-engine/runs/conversion-quality").glob("*.json"))
    assert len(diagnostics) == 1
    diagnostic = json.loads(diagnostics[0].read_text(encoding="utf-8"))
    assert diagnostic["result"] == "CONVERSION_QUALITY_BLOCKED"
    assert diagnostic["llm_calls"] == 0
    assert all(value == 0 for value in diagnostic["formal_writes"].values())
