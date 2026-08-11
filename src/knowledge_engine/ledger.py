from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .models import ConversionIdentity, IngestedSource
from .renderer import RenderedBundle
from .utils import now_iso, sha256_bytes, sha256_text


ASSET_LEDGER_JSON = Path("05_Knowledge/Audit/ASSET_LEDGER.json")


@dataclass
class AssetLedger:
    data: dict[str, Any]

    @classmethod
    def empty(cls) -> "AssetLedger":
        return cls({
            "schema_version": 1,
            "engine_version": "3.3.0",
            "revision": 0,
            "previous_ledger_sha256": None,
            "updated_at": None,
            "entries": {},
            "skill_versions": {},
        })

    @classmethod
    def load(cls, vault: Path) -> "AssetLedger":
        path = vault / ASSET_LEDGER_JSON
        if not path.is_file():
            return cls.empty()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Asset ledger cannot be read: {exc}") from exc
        cls.validate(data)
        return cls(data)

    def add_bundle(
        self,
        bundle: RenderedBundle,
        source: IngestedSource,
        conversion: ConversionIdentity,
        source_title: str,
        transaction_revision: int,
        previous_ledger_sha256: str | None,
    ) -> dict[str, Any]:
        immutable: list[dict[str, str]] = []

        def add(role: str, path: Path, content: str | bytes) -> None:
            data = content if isinstance(content, bytes) else content.encode("utf-8")
            immutable.append({"role": role, "path": path.as_posix(), "sha256": sha256_bytes(data)})

        add("original", bundle.original_path, bundle.binary_artifacts[bundle.original_path])
        for filename, role in (
            ("SOURCE_TEXT.md", "source-text"),
            ("CONVERSION.json", "conversion-metadata"),
            ("EVIDENCE_CATALOG.json", "evidence-catalog"),
            ("SOURCE_IDENTITY.json", "source-identity"),
            ("STRUCTURE_NORMALIZATION.json", "structure-normalization"),
            ("CONCEPT_CANONICALIZATION.json", "concept-canonicalization"),
            ("DISTILLATION_MANIFEST.json", "distillation-manifest"),
        ):
            path = bundle.conversion_root / filename
            add(role, path, bundle.artifacts[path])
        for skill_path in sorted(bundle.version_skill_paths):
            version_root = skill_path.parent
            add("skill-version", skill_path, bundle.artifacts[skill_path])
            add("skill-evidence", version_root / "references/evidence.json", bundle.artifacts[version_root / "references/evidence.json"])
            add("skill-version-record", version_root / "VERSION.json", bundle.artifacts[version_root / "VERSION.json"])
        manifest_path = bundle.conversion_root / "DISTILLATION_MANIFEST.json"
        entry = {
            "source_id": source.source_id,
            "conversion_id": conversion.conversion_id,
            "source_title": source_title,
            "source_text_sha256": conversion.normalized_text_sha256,
            "transaction_revision": transaction_revision,
            "upper_manifest_path": manifest_path.as_posix(),
            "upper_manifest_sha256": next(item["sha256"] for item in immutable if item["role"] == "distillation-manifest"),
            "assets": immutable,
            "created_at": now_iso(),
        }
        existing = self.data["entries"].get(conversion.conversion_id)
        if existing is not None and existing != entry:
            # Timestamp/revision are not allowed to silently mutate an immutable entry.
            stable_existing = {**existing, "created_at": entry["created_at"], "transaction_revision": transaction_revision}
            if stable_existing != entry:
                raise ValueError("Asset ledger conversion entry conflict.")
            return existing
        self.data["entries"][conversion.conversion_id] = entry
        for skill_path in sorted(bundle.version_skill_paths):
            version_root = skill_path.parent
            version_payload = json.loads(bundle.artifacts[version_root / "VERSION.json"])
            self.add_skill_version(
                skill_id=str(version_payload["skill_id"]),
                name=str(version_payload["name"]),
                version=str(version_payload["version"]),
                source_conversions=[conversion.conversion_id],
                transaction_revision=transaction_revision,
                skill_path=skill_path,
                skill_content=bundle.artifacts[skill_path],
                evidence_path=version_root / "references/evidence.json",
                evidence_content=bundle.artifacts[version_root / "references/evidence.json"],
                version_path=version_root / "VERSION.json",
                version_content=bundle.artifacts[version_root / "VERSION.json"],
            )
        self.data["revision"] = transaction_revision
        self.data["previous_ledger_sha256"] = previous_ledger_sha256
        self.data["updated_at"] = now_iso()
        self.validate(self.data)
        return entry

    def add_skill_version(
        self,
        *,
        skill_id: str,
        name: str,
        version: str,
        source_conversions: list[str],
        transaction_revision: int,
        skill_path: Path,
        skill_content: str,
        evidence_path: Path,
        evidence_content: str,
        version_path: Path,
        version_content: str,
    ) -> dict[str, Any]:
        assets = [
            {"role": "skill-version", "path": skill_path.as_posix(), "sha256": sha256_text(skill_content)},
            {"role": "skill-evidence", "path": evidence_path.as_posix(), "sha256": sha256_text(evidence_content)},
            {"role": "skill-version-record", "path": version_path.as_posix(), "sha256": sha256_text(version_content)},
        ]
        key = f"{skill_id}@{version}"
        record = {
            "skill_id": skill_id, "name": name, "version": version,
            "source_conversions": sorted(set(source_conversions)),
            "transaction_revision": transaction_revision,
            "assets": assets, "created_at": now_iso(),
        }
        existing = self.data.setdefault("skill_versions", {}).get(key)
        if existing is not None:
            stable = {**existing, "created_at": record["created_at"], "transaction_revision": transaction_revision}
            if stable != record:
                raise ValueError("Asset ledger Skill version conflict.")
            return existing
        self.data["skill_versions"][key] = record
        self.data["revision"] = transaction_revision
        self.data["updated_at"] = now_iso()
        return record

    def to_json(self) -> str:
        self.validate(self.data)
        return json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    @classmethod
    def validate(cls, payload: dict[str, Any]) -> None:
        errors = sorted(Draft202012Validator(cls.schema()).iter_errors(payload), key=lambda item: list(item.path))
        if errors:
            raise ValueError(f"Asset ledger schema invalid at {errors[0].json_path}: {errors[0].message}")

    @staticmethod
    def schema() -> dict[str, Any]:
        asset = {
            "type": "object", "additionalProperties": False,
            "required": ["role", "path", "sha256"],
            "properties": {
                "role": {"enum": [
                    "original", "source-text", "conversion-metadata", "evidence-catalog",
                    "source-identity", "structure-normalization", "concept-canonicalization",
                    "distillation-manifest", "skill-version", "skill-evidence", "skill-version-record",
                ]},
                "path": {"type": "string", "minLength": 1},
                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
        }
        entry = {
            "type": "object", "additionalProperties": False,
            "required": [
                "source_id", "conversion_id", "source_title", "source_text_sha256",
                "transaction_revision", "upper_manifest_path", "upper_manifest_sha256",
                "assets", "created_at",
            ],
            "properties": {
                "source_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "conversion_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "source_title": {"type": "string", "minLength": 1},
                "source_text_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "transaction_revision": {"type": "integer", "minimum": 1},
                "upper_manifest_path": {"type": "string", "minLength": 1},
                "upper_manifest_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "assets": {"type": "array", "minItems": 4, "items": asset},
                "created_at": {"type": "string"},
            },
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object", "additionalProperties": False,
            "required": [
                "schema_version", "engine_version", "revision", "previous_ledger_sha256",
                "updated_at", "entries", "skill_versions",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "engine_version": {"enum": ["3.1.0", "3.2.0", "3.3.0"]},
                "revision": {"type": "integer", "minimum": 0},
                "previous_ledger_sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
                "updated_at": {"type": ["string", "null"]},
                "entries": {"type": "object", "additionalProperties": entry},
                "skill_versions": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object", "additionalProperties": False,
                        "required": ["skill_id", "name", "version", "source_conversions", "transaction_revision", "assets", "created_at"],
                        "properties": {
                            "skill_id": {"type": "string"}, "name": {"type": "string"}, "version": {"type": "string"},
                            "source_conversions": {"type": "array", "minItems": 1, "items": HASH_SCHEMA()},
                            "transaction_revision": {"type": "integer", "minimum": 1},
                            "assets": {"type": "array", "minItems": 3, "maxItems": 3, "items": asset},
                            "created_at": {"type": "string"},
                        },
                    },
                },
            },
        }


def HASH_SCHEMA() -> dict[str, Any]:
    return {"type": "string", "pattern": "^[0-9a-f]{64}$"}
