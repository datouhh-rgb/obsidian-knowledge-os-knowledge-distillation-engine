from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import ConversionIdentity, IngestedSource, SourceIdentity
from .utils import canonical_json, normalize_text, now_iso, safe_filename, sha256_bytes, sha256_text


def source_id_for_bytes(raw: bytes) -> str:
    return sha256_bytes(raw)


def source_id_for(original_hash: str, source_format: str, edition_id: str | None = None) -> str:
    """Keep legacy identity for unspecified editions; explicitly separate editions."""
    edition = (edition_id or "").strip()
    if not edition:
        return original_hash
    return sha256_text(canonical_json({
        "original_hash": original_hash,
        "format": source_format.casefold(),
        "edition_id": edition,
    }))


def conversion_identity(source: IngestedSource, converted_text: str | None = None) -> ConversionIdentity:
    metadata = dict(source.converter_metadata)
    converter = str(metadata.get("converter", "identity"))
    version = str(metadata.get("version", metadata.get("converter_version", "1")))
    config = metadata.get("config", {})
    if not isinstance(config, dict):
        config = {"value": config}
    normalized = normalize_text(source.text if converted_text is None else converted_text)
    payload = {
        "source_id": source.source_id,
        "normalized_source_text": normalized,
        "converter": converter,
        "converter_version": version,
        "converter_config": config,
    }
    return ConversionIdentity(
        source_id=source.source_id,
        conversion_id=sha256_text(canonical_json(payload)),
        normalized_text_sha256=sha256_text(normalized),
        converter=converter,
        converter_version=version,
        converter_config=config,
        warnings=tuple(source.warnings),
    )


def build_source_identity(
    source: IngestedSource,
    conversion: ConversionIdentity,
    edition_id: str | None = None,
    created_at: str | None = None,
) -> SourceIdentity:
    metadata = dict(source.converter_metadata)
    expected_source_id = source_id_for(source.original_hash, source.source_kind, edition_id)
    if conversion.source_id != expected_source_id:
        raise ValueError("Conversion identity is not bound to the expected original/edition identity.")
    converter = str(metadata.get("converter", "identity"))
    converter_version = str(metadata.get("version", metadata.get("converter_version", "1")))
    return SourceIdentity(
        source_id=expected_source_id,
        original_hash=source.original_hash,
        format=source.source_kind,
        converter=converter,
        converter_version=converter_version,
        conversion_id=conversion.conversion_id,
        conversion_hash=conversion.conversion_hash,
        edition_id=(edition_id or "unspecified"),
        created_at=created_at or now_iso(),
    )


def bind_source_identity(source: IngestedSource, edition_id: str | None = None) -> IngestedSource:
    source.identity_source_id = source_id_for(source.original_hash, source.source_kind, edition_id)
    source.edition_id = edition_id
    return source


def source_directory(title: str, source_id: str, edition_id: str | None = None) -> Path:
    readable = safe_filename(title, "untitled-source", max_length=64)
    edition = safe_filename(edition_id, "", max_length=24) if edition_id else ""
    suffix = f"--{source_id[:12]}"
    if edition:
        suffix = f"--{edition}{suffix}"
    return Path("06_Sources/Books") / f"{readable}{suffix}"


def conversion_directory(source_root: Path, conversion_id: str) -> Path:
    return source_root / "conversions" / conversion_id


def source_index_record(source: IngestedSource, title: str, edition_id: str | None, source_root: Path) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "title": title,
        "aliases": [title],
        "edition_id": edition_id,
        "original_name": source.original_name,
        "input_ref": source.input_ref,
        "source_root": source_root.as_posix(),
    }
