from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .evidence import EvidenceCatalog, EvidenceUniverse
from .exposure import SkillExposureManager
from .graph import ConceptGraph
from .ledger import ASSET_LEDGER_JSON, AssetLedger
from .pipeline import SOURCE_INDEX
from .registry import SkillRegistry
from .schemas_v31 import (
    CONVERSION_SCHEMA,
    EVIDENCE_CATALOG_SCHEMA,
    MANIFEST_SCHEMA,
    SOURCE_IDENTITY_SCHEMA,
    VERSION_SCHEMA,
    require_schema,
)
from .transaction import STATE_PATH, VaultTransaction
from .trigger_eval import TriggerEvalValidator
from .utils import canonical_json, normalize_text, sha256_file, sha256_text


@dataclass(frozen=True)
class IntegrityResult:
    status: str
    checks: tuple[dict[str, Any], ...]
    recovered_transactions: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": list(self.checks),
            "recovered_transactions": list(self.recovered_transactions),
        }


class IntegrityReconciler:
    def __init__(self, vault: Path) -> None:
        self.vault = vault.resolve()

    def reconcile(self, repair: bool = False) -> IntegrityResult:
        recovered = tuple(VaultTransaction.recover(self.vault)) if repair else ()
        checks: list[dict[str, Any]] = []
        state = self._load_state(checks)
        ledger = self._load_ledger(checks, state)
        source_index = self._load_source_index(checks)
        catalogs: list[EvidenceCatalog] = []
        seen_conversions: set[str] = set()
        conversion_paths = (
            sorted((self.vault / "06_Sources/Books").rglob("CONVERSION.json"))
            if (self.vault / "06_Sources/Books").is_dir() else []
        )
        for conversion_path in conversion_paths:
            try:
                metadata = json.loads(conversion_path.read_text(encoding="utf-8"))
                if metadata.get("status") == "blocked":
                    continue
                require_schema(metadata, CONVERSION_SCHEMA, "CONVERSION.json")
                source_text_path = self.vault / str(metadata["source_text_path"])
                original_path = self.vault / str(metadata["original_path"])
                source_text = source_text_path.read_text(encoding="utf-8")
                source_id = str(metadata["source_id"])
                conversion_id = str(metadata["conversion_id"])
                seen_conversions.add(conversion_id)
                original_hash = str(metadata.get("original_hash", source_id))
                self._check(checks, original_path.is_file() and sha256_file(original_path) == original_hash, "original-hash", str(original_path))
                normalized_sha = sha256_text(normalize_text(source_text))
                self._check(checks, normalized_sha == metadata["normalized_text_sha256"] == metadata["source_text_sha256"], "source-text-hash", str(source_text_path))
                identity_payload = {
                    "source_id": source_id,
                    "normalized_source_text": normalize_text(source_text),
                    "converter": metadata["converter"],
                    "converter_version": metadata["converter_version"],
                    "converter_config": metadata["converter_config"],
                }
                self._check(checks, sha256_text(canonical_json(identity_payload)) == conversion_id, "conversion-id", str(conversion_path))

                identity_path = conversion_path.parent / "SOURCE_IDENTITY.json"
                if metadata.get("engine_version") in {"3.2.0", "3.3.0"}:
                    identity = json.loads(identity_path.read_text(encoding="utf-8"))
                    require_schema(identity, SOURCE_IDENTITY_SCHEMA, "SOURCE_IDENTITY.json")
                    self._check(
                        checks,
                        identity["source_id"] == source_id
                        and identity["original_hash"] == original_hash
                        and identity["conversion_id"] == conversion_id
                        and identity["conversion_hash"] == normalized_sha
                        and identity["converter"] == metadata["converter"]
                        and identity["converter_version"] == metadata["converter_version"],
                        "source-identity-cross-bind", str(identity_path),
                    )

                catalog_path = conversion_path.parent / "EVIDENCE_CATALOG.json"
                catalog_payload = json.loads(catalog_path.read_text(encoding="utf-8"))
                require_schema(catalog_payload, EVIDENCE_CATALOG_SCHEMA, "EVIDENCE_CATALOG.json")
                self._check(
                    checks,
                    catalog_payload["source_id"] == source_id
                    and catalog_payload["conversion_id"] == conversion_id
                    and catalog_payload["source_title"] == metadata["source_title"]
                    and catalog_payload["source_text_sha256"] == normalized_sha
                    and catalog_payload["original_path"] == metadata["original_path"],
                    "catalog-conversion-cross-bind", str(catalog_path),
                )
                catalog = EvidenceCatalog.from_dict(catalog_payload, source_text)
                catalogs.append(catalog)
                report = catalog.verify_integrity()
                self._check(checks, report.passed, "evidence-integrity", str(catalog_path), [item.__dict__ for item in report.issues])

                manifest_path = conversion_path.parent / "DISTILLATION_MANIFEST.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                require_schema(manifest, MANIFEST_SCHEMA, "DISTILLATION_MANIFEST.json")
                self._check(
                    checks,
                    manifest["source_id"] == source_id
                    and manifest["conversion_id"] == conversion_id
                    and manifest["title"] == metadata["source_title"]
                    and manifest["original_path"] == metadata["original_path"]
                    and manifest["original_sha256"] == original_hash
                    and manifest["source_text_sha256"] == normalized_sha,
                    "manifest-conversion-cross-bind", str(manifest_path),
                )
                manifest_hash_ok = True
                manifest_hash_detail: list[str] = []
                for relative, expected in manifest["artifact_hashes"].items():
                    target = self.vault / relative
                    if not target.is_file() or sha256_file(target) != expected:
                        manifest_hash_ok = False; manifest_hash_detail.append(relative)
                self._check(checks, manifest_hash_ok, "manifest-artifact-hashes", str(manifest_path), manifest_hash_detail)
                self._check_source_index(checks, source_index, metadata, conversion_path)
                self._check_ledger_entry(checks, ledger, metadata, manifest_path)
            except Exception as exc:
                self._check(checks, False, "conversion-load", str(conversion_path), str(exc))

        universe = EvidenceUniverse.from_catalogs(catalogs)
        try:
            registry = SkillRegistry.load(self.vault)
            for record in registry.records.values():
                legacy_record = all(item.get("kind") == "legacy-v2" for item in record["versions"].values())
                for version, version_record in record["versions"].items():
                    if version_record.get("kind") == "legacy-v2":
                        continue
                    skill_path = self.vault / version_record["artifact_path"]
                    version_root = skill_path.parent
                    evidence_path = version_root / "references/evidence.json"
                    version_path = version_root / "VERSION.json"
                    self._check(checks, skill_path.is_file() and sha256_file(skill_path) == version_record.get("artifact_sha256"), "skill-version-hash", str(skill_path))
                    self._check(checks, evidence_path.is_file() and sha256_file(evidence_path) == version_record.get("evidence_file_sha256"), "skill-evidence-hash", str(evidence_path))
                    version_payload = json.loads(version_path.read_text(encoding="utf-8"))
                    require_schema(version_payload, VERSION_SCHEMA, "VERSION.json")
                    self._check(
                        checks,
                        version_record.get("version_record_sha256") == sha256_file(version_path)
                        and version_payload["skill_id"] == record["skill_id"]
                        and version_payload["name"] == record["canonical_name"]
                        and version_payload["version"] == version
                        and version_payload["skill_sha256"] == version_record.get("artifact_sha256")
                        and version_payload["evidence_sha256"] == version_record.get("evidence_file_sha256"),
                        "skill-version-record", str(version_path),
                    )
                    missing = [item for item in version_record.get("evidence_bindings", []) if universe.get(item) is None]
                    self._check(checks, not missing, "skill-evidence-binding", f"{record['canonical_name']}@{version}", missing)
                    for evaluation in record.get("lifecycle", {}).get(version, {}).get("evals", []):
                        try:
                            evaluation_path = self.vault / evaluation["evaluation_path"]
                            payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
                            TriggerEvalValidator.validate_run(self.vault, payload, record["canonical_name"], version)
                            self._check(checks, evaluation.get("artifact_sha256") == version_record.get("artifact_sha256"), "trigger-eval-binding", str(evaluation_path))
                        except Exception as exc:
                            self._check(checks, False, "trigger-eval-integrity", f"{record['canonical_name']}@{version}", str(exc))
                if not legacy_record:
                    current = record["current_version"]
                    current_record = record["versions"][current]
                    root_skill = self.vault / "05_Knowledge/Skills" / record["canonical_name"] / "SKILL.md"
                    self._check(checks, root_skill.is_file() and sha256_file(root_skill) == current_record.get("artifact_sha256"), "current-materialization", str(root_skill))
            for event_id, event in registry.data.get("usage_events", {}).items():
                review = self.vault / "10_Reviews/Skill-Usage" / event["name"] / event["version"] / f"{event_id}.md"
                self._check(checks, review.is_file(), "usage-review", str(review))
        except Exception as exc:
            self._check(checks, False, "registry-load", "REGISTRY_V3.json", str(exc))
        try:
            graph = ConceptGraph.load(self.vault)
            governance = graph.validate_governance()
            self._check(checks, not governance.errors, "graph-governance", "concept-graph.v3.json", [item.__dict__ for item in governance.issues])
            for edge in graph.edges.values():
                missing = [item for item in edge.get("evidence_ids", []) if universe.get(item) is None]
                self._check(checks, not missing, "graph-evidence", edge["id"], missing)
        except Exception as exc:
            self._check(checks, False, "graph-load", "concept-graph.v3.json", str(exc))
        try:
            if (self.vault / "05_Knowledge/Skills").is_dir():
                exposure = SkillExposureManager(self.vault).reconcile(repair=repair)
                self._check(checks, exposure["issue_count"] == 0, "exposure-reconcile", "EXPOSURES.json", exposure)
        except Exception as exc:
            self._check(checks, False, "exposure-load", "EXPOSURES.json", str(exc))
        if seen_conversions:
            ledger_entries = set(ledger.data["entries"]) if ledger is not None else set()
            self._check(checks, seen_conversions.issubset(ledger_entries), "ledger-conversion-coverage", str(ASSET_LEDGER_JSON), sorted(seen_conversions - ledger_entries))
        status = "passed" if all(item["passed"] for item in checks) else "failed"
        return IntegrityResult(status, tuple(checks), recovered)

    def _load_state(self, checks: list[dict[str, Any]]) -> dict[str, Any]:
        path = self.vault / STATE_PATH
        if not path.is_file():
            return {"revision": 0}
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            self._check(checks, isinstance(state.get("revision"), int), "state-schema", str(path))
            return state
        except Exception as exc:
            self._check(checks, False, "state-load", str(path), str(exc)); return {"revision": 0}

    def _load_ledger(self, checks: list[dict[str, Any]], state: dict[str, Any]) -> AssetLedger | None:
        path = self.vault / ASSET_LEDGER_JSON
        if not path.is_file():
            return None
        try:
            ledger = AssetLedger.load(self.vault)
            self._check(checks, state.get("asset_ledger_sha256") == sha256_file(path), "upper-ledger-hash", str(path))
            self._check(checks, int(ledger.data["revision"]) <= int(state.get("revision", 0)), "upper-ledger-revision", str(path))
            for entry in ledger.data["entries"].values():
                bad = [
                    item["path"] for item in entry["assets"]
                    if not (self.vault / item["path"]).is_file() or sha256_file(self.vault / item["path"]) != item["sha256"]
                ]
                self._check(checks, not bad, "asset-ledger-byte-hashes", str(path), bad)
            for skill_version in ledger.data.get("skill_versions", {}).values():
                bad = [
                    item["path"] for item in skill_version["assets"]
                    if not (self.vault / item["path"]).is_file() or sha256_file(self.vault / item["path"]) != item["sha256"]
                ]
                self._check(checks, not bad, "asset-ledger-skill-version-hashes", str(path), bad)
            return ledger
        except Exception as exc:
            self._check(checks, False, "asset-ledger-load", str(path), str(exc)); return None

    def _load_source_index(self, checks: list[dict[str, Any]]) -> dict[str, Any]:
        path = self.vault / SOURCE_INDEX
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            self._check(checks, isinstance(value.get("sources"), dict) and isinstance(value.get("titles"), dict), "source-index-schema", str(path))
            return value
        except Exception as exc:
            self._check(checks, False, "source-index-load", str(path), str(exc)); return {}

    def _check_source_index(self, checks: list[dict[str, Any]], index: dict[str, Any], metadata: dict[str, Any], path: Path) -> None:
        source = index.get("sources", {}).get(metadata["source_id"], {})
        passed = (
            source.get("title") == metadata["source_title"]
            and metadata["conversion_id"] in source.get("conversions", [])
            and metadata["source_id"] in index.get("titles", {}).get(metadata["source_title"].casefold(), [])
        )
        self._check(checks, passed, "source-index-cross-bind", str(path))

    def _check_ledger_entry(self, checks: list[dict[str, Any]], ledger: AssetLedger | None, metadata: dict[str, Any], manifest_path: Path) -> None:
        entry = None if ledger is None else ledger.data["entries"].get(metadata["conversion_id"])
        passed = bool(entry) and (
            entry["source_id"] == metadata["source_id"]
            and entry["source_title"] == metadata["source_title"]
            and entry["source_text_sha256"] == metadata["source_text_sha256"]
            and entry["upper_manifest_path"] == manifest_path.relative_to(self.vault).as_posix()
            and entry["upper_manifest_sha256"] == sha256_file(manifest_path)
        )
        self._check(checks, passed, "asset-ledger-cross-bind", str(manifest_path))

    @staticmethod
    def _check(checks: list[dict[str, Any]], passed: bool, code: str, path: str, detail: Any = None) -> None:
        checks.append({"code": code, "passed": bool(passed), "path": path, "detail": detail})
