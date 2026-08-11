from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator


HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}


SOURCE_IDENTITY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": [
        "schema_version", "source_id", "original_hash", "format", "converter",
        "converter_version", "conversion_id", "conversion_hash", "edition_id", "created_at",
    ],
    "properties": {
        "schema_version": {"const": 1}, "source_id": HASH, "original_hash": HASH,
        "format": {"type": "string", "minLength": 1}, "converter": {"type": "string", "minLength": 1},
        "converter_version": {"type": "string", "minLength": 1}, "conversion_id": HASH,
        "conversion_hash": HASH, "edition_id": {"type": "string", "minLength": 1},
        "created_at": {"type": "string", "minLength": 1},
    },
}


CONVERSION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": [
        "schema_version", "source_id", "conversion_id", "normalized_text_sha256",
        "converter", "converter_version", "converter_config", "warnings", "source_title",
        "original_path", "source_text_path", "source_text_sha256",
    ],
    "properties": {
        "schema_version": {"enum": [3, 4]}, "engine_version": {"type": "string"},
        "source_id": HASH, "original_hash": HASH, "conversion_id": HASH, "conversion_hash": HASH,
        "normalized_text_sha256": HASH, "converter": {"type": "string", "minLength": 1},
        "converter_version": {"type": "string", "minLength": 1},
        "converter_config": {"type": "object"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "source_title": {"type": "string", "minLength": 1},
        "original_path": {"type": "string", "minLength": 1},
        "source_text_path": {"type": "string", "minLength": 1},
        "source_text_sha256": HASH,
    },
}


EVIDENCE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": [
        "evidence_id", "source_id", "conversion_id", "section", "locator",
        "start_line", "end_line", "excerpt", "excerpt_sha256", "original_path",
    ],
    "properties": {
        "evidence_id": {"type": "string", "pattern": "^ev-[0-9a-f]{64}$"},
        "source_id": HASH, "conversion_id": HASH,
        "section": {"type": "string"}, "locator": {"type": "string", "pattern": "^lines:[0-9]+-[0-9]+$"},
        "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1},
        "excerpt": {"type": "string", "minLength": 1}, "excerpt_sha256": HASH,
        "original_path": {"type": "string"},
        "normalized_locator": {"type": "string", "pattern": "^lines:[0-9]+-[0-9]+$"},
        "conversion_locator": {"type": "string", "pattern": "^lines:[0-9]+-[0-9]+$"},
    },
}


EVIDENCE_CATALOG_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": [
        "schema_version", "source_title", "source_id", "conversion_id",
        "source_text_sha256", "original_path", "items",
    ],
    "properties": {
        "schema_version": {"const": 3}, "source_title": {"type": "string", "minLength": 1},
        "source_id": HASH, "conversion_id": HASH, "source_text_sha256": HASH,
        "original_path": {"type": "string"},
        "items": {"type": "array", "items": EVIDENCE_ITEM_SCHEMA},
    },
}


MANIFEST_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": [
        "schema_version", "engine_version", "title", "source_id", "conversion_id",
        "original_path", "original_sha256", "source_text_sha256", "provider",
        "provider_provenance", "warnings", "artifacts", "artifact_hashes", "skills",
    ],
    "properties": {
        "schema_version": {"enum": [4, 5]}, "engine_version": {"enum": ["3.1.0", "3.2.0", "3.3.0"]},
        "title": {"type": "string", "minLength": 1}, "source_id": HASH, "conversion_id": HASH,
        "original_path": {"type": "string", "minLength": 1}, "original_sha256": HASH,
        "source_text_sha256": HASH, "provider": {"type": "string", "minLength": 1},
        "provider_provenance": {"type": "object"}, "warnings": {"type": "array", "items": {"type": "string"}},
        "artifacts": {"type": "array", "items": {"type": "string"}},
        "artifact_hashes": {"type": "object", "additionalProperties": HASH},
        "skills": {"type": "array", "items": {"type": "string"}},
    },
}


VERSION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": [
        "schema_version", "engine_version", "skill_id", "name", "version", "status",
        "skill_sha256", "evidence_sha256", "source_id", "conversion_id",
    ],
    "properties": {
        "schema_version": {"const": 4}, "engine_version": {"enum": ["3.1.0", "3.2.0", "3.3.0"]},
        "skill_id": {"type": "string"}, "name": {"type": "string", "minLength": 1},
        "version": {"type": "string"}, "status": {"type": "string"},
        "skill_sha256": HASH, "evidence_sha256": HASH, "source_id": HASH, "conversion_id": HASH,
        "proposal_id": {"type": "string"},
    },
}


def require_schema(payload: Any, schema: dict[str, Any], label: str) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        raise ValueError(f"{label} schema invalid at {errors[0].json_path}: {errors[0].message}")
