from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import REGISTRY_JSON, REGISTRY_MD, SkillRegistry
from .transaction import VaultTransaction
from .utils import canonical_json, now_iso, sha256_file, sha256_text


MIGRATION_ROOT = Path(".knowledge-engine/migrations")
LEGACY_ROOT = Path("05_Knowledge/Legacy/V2")
V2_REGISTRY = Path("05_Knowledge/Skills/REGISTRY.json")
V2_GRAPH = Path("05_Knowledge/Graph/concept-graph.json")


@dataclass(frozen=True)
class MigrationResult:
    migration_id: str
    status: str
    plan: dict[str, Any]
    written_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"migration_id": self.migration_id, "status": self.status, "plan": self.plan, "written_paths": list(self.written_paths)}


class V2MigrationService:
    def __init__(self, vault: Path) -> None:
        self.vault = vault.resolve()
        if not (self.vault / "05_Knowledge").is_dir() or not (self.vault / "06_Sources").is_dir():
            raise ValueError("目标不是现有 Knowledge OS Vault。")

    def plan(self) -> dict[str, Any]:
        source_dirs = [path for path in (self.vault / "06_Sources/Books").iterdir() if path.is_dir()] if (self.vault / "06_Sources/Books").is_dir() else []
        source_groups: dict[str, list[str]] = {}
        for path in source_dirs:
            logical_name = re.sub(r"--[0-9a-f]{8,64}$", "", path.name, flags=re.IGNORECASE).casefold()
            source_groups.setdefault(logical_name, []).append(str(path.relative_to(self.vault)).replace("\\", "/"))
        duplicate_sources = [items for items in source_groups.values() if len(items) > 1]
        evidence_risks: list[dict[str, Any]] = []
        evidence_files = list((self.vault / "06_Sources/Books").rglob("EVIDENCE_CATALOG.json")) if (self.vault / "06_Sources/Books").is_dir() else []
        for path in evidence_files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                evidence_risks.append({"path": str(path.relative_to(self.vault)), "risk": "malformed"})
                continue
            serialized = canonical_json(payload)
            if "conversion_id" not in serialized or "source_id" not in serialized:
                evidence_risks.append({"path": str(path.relative_to(self.vault)), "risk": "missing-v3-identity"})
        v2_registry = self._load_json_optional(self.vault / V2_REGISTRY)
        skill_risks: list[dict[str, Any]] = []
        status_inconsistencies: list[dict[str, Any]] = []
        for name, record in (v2_registry or {}).get("skills", {}).items():
            if len(record.get("source_books", [])) > 1 or record.get("history"):
                skill_risks.append({"skill": name, "risk": "multi-source-or-overwrite-history"})
            agent_path = self.vault / "05_Knowledge/Skills" / name / "agents/openai.yaml"
            implicit = None
            if agent_path.is_file():
                match = re.search(r"allow_implicit_invocation:\s*(true|false)", agent_path.read_text(encoding="utf-8"))
                implicit = None if not match else match.group(1) == "true"
            if record.get("status") == "active" and implicit is False:
                status_inconsistencies.append({"skill": name, "status": "active", "implicit": False})
        same_title_skills: dict[str, list[str]] = {}
        for name, record in (v2_registry or {}).get("skills", {}).items():
            same_title_skills.setdefault(str(record.get("title", name)).casefold(), []).append(name)
        collisions = [items for items in same_title_skills.values() if len(items) > 1]
        snapshot = self._v2_snapshot()
        migration_id = "migrate-v2-" + sha256_text(canonical_json(snapshot))[:20]
        return {
            "schema_version": 3, "migration_id": migration_id, "created_at": now_iso(),
            "v2_snapshot": snapshot, "duplicate_source_directories": duplicate_sources,
            "same_title_skills": collisions, "overwrite_style_skills": skill_risks,
            "evidence_rebinding_risks": evidence_risks, "status_inconsistencies": status_inconsistencies,
            "actions": ["create V3 registry records as legacy/review", "preserve every V2 file byte-for-byte", "write migration manifest and link index"],
            "risk_count": len(duplicate_sources) + len(collisions) + len(skill_risks) + len(evidence_risks) + len(status_inconsistencies),
        }

    def dry_run(self) -> MigrationResult:
        plan = self.plan()
        return MigrationResult(plan["migration_id"], "dry-run", plan)

    def apply(self) -> MigrationResult:
        plan = self.plan()
        migration_id = plan["migration_id"]
        record_path = self.vault / MIGRATION_ROOT / f"{migration_id}.json"
        if record_path.is_file():
            existing = json.loads(record_path.read_text(encoding="utf-8"))
            if existing.get("status") == "applied":
                return MigrationResult(migration_id, "idempotent", plan)
        with VaultTransaction(self.vault, "migrate-v2", idempotency_key=migration_id) as tx:
            if tx.duplicate:
                return MigrationResult(migration_id, "idempotent", plan)
            registry = SkillRegistry.load(self.vault)
            v2_registry = self._load_json_optional(self.vault / V2_REGISTRY) or {"skills": {}}
            created: list[str] = []
            for name, old in v2_registry.get("skills", {}).items():
                legacy_key = f"legacy-v2:{name}:{old.get('version', 'unknown')}"
                existing = next((item for item in registry.records.values() if legacy_key in item.get("aliases", [])), None)
                if existing:
                    continue
                skill_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"obsidian-knowledge-os:{legacy_key}"))
                canonical = self._legacy_name(name, registry)
                skill_path = Path(str(old.get("skill_path", f"05_Knowledge/Skills/{name}/SKILL.md")))
                skill_file = self.vault / skill_path
                markdown = skill_file.read_text(encoding="utf-8") if skill_file.is_file() else ""
                version = "0.0.0"
                sources = [{
                    "source_id": str(item.get("sha256", "legacy-unbound")), "conversion_id": "legacy-unbound",
                    "book_title": str(item.get("book_title", "")), "original_path": str(item.get("input_ref", "")),
                    "evidence_ids": list(item.get("evidence_ids", [])),
                } for item in old.get("source_books", [])]
                registry.records[skill_id] = {
                    "skill_id": skill_id, "canonical_name": canonical, "title": str(old.get("title", name)),
                    "aliases": [legacy_key, name], "created_at": now_iso(), "current_version": version,
                    "versions": {version: {
                        "skill_id": skill_id, "version": version, "kind": "legacy-v2", "created_at": now_iso(),
                        "content": {"legacy_skill_markdown": markdown, "v2_record": old},
                        "semantic_text": str(old.get("semantic_text", "")),
                        "content_fingerprint": sha256_text(markdown), "evidence_bindings": [], "sources": sources,
                        "provider": str(old.get("provider", "legacy")), "provider_provenance": {"migration": migration_id},
                        "semantic_assessments": [], "quality_at_creation": {"status": "legacy-review"},
                        "artifact_path": skill_path.as_posix(), "artifact_sha256": sha256_file(skill_file) if skill_file.is_file() else None,
                        "evidence_file_sha256": None,
                    }},
                    "lifecycle": {version: {
                        "status": "legacy-review", "approvals": [], "decisions": [], "evals": [], "quality": None,
                        "usage": {"invocation_count": 0, "outcomes": {"success": 0, "partial": 0, "failed": 0}, "feedback": [], "projects": []},
                        "exposures": [], "updated_at": now_iso(),
                    }}, "relations": [],
                }
                registry.by_name[canonical] = skill_id
                created.append(canonical)
            before = self._backup_mutable()
            link_index = {
                "schema_version": 3, "migration_id": migration_id, "legacy_assets": plan["v2_snapshot"],
                "created_skills": created, "policy": "V2 assets are referenced in place and remain byte-identical.",
            }
            record = {
                "schema_version": 3, "migration_id": migration_id, "status": "applied",
                "applied_at": now_iso(), "plan": plan, "before": before,
                "created_paths": [REGISTRY_JSON.as_posix(), REGISTRY_MD.as_posix(), (LEGACY_ROOT / migration_id / "MIGRATION_LINKS.json").as_posix()],
            }
            tx.stage_text(REGISTRY_JSON, registry.to_json())
            tx.stage_text(REGISTRY_MD, registry.to_markdown())
            tx.stage_text(LEGACY_ROOT / migration_id / "MIGRATION_LINKS.json", json.dumps(link_index, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            tx.stage_text(MIGRATION_ROOT / f"{migration_id}.json", json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            committed = tx.commit({"migration_id": migration_id, "created_skills": created})
            record["after_hashes"] = self._hash_paths(record["created_paths"])
            record["transaction_id"] = committed.transaction_id
            # This management update is outside the migrated asset set and does not alter V2 bytes.
            from .utils import atomic_write
            atomic_write(record_path, json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            return MigrationResult(migration_id, "applied", plan, tuple(str(path) for path in committed.written_paths))

    def rollback(self, migration_id: str) -> MigrationResult:
        record_path = self.vault / MIGRATION_ROOT / f"{migration_id}.json"
        if not record_path.is_file():
            raise ValueError(f"Migration 不存在：{migration_id}")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        plan = record["plan"]
        if record.get("status") == "rolled_back":
            return MigrationResult(migration_id, "idempotent-rollback", plan)
        current = self._hash_paths(record.get("created_paths", []))
        expected = record.get("after_hashes", {})
        if current != expected:
            raise ValueError("迁移后 V3 资产已变化；为防覆盖后续工作，拒绝自动 rollback。")
        with VaultTransaction(self.vault, "rollback-v2-migration", idempotency_key=f"rollback:{migration_id}") as tx:
            before = record.get("before", {})
            for relative in record.get("created_paths", []):
                if relative in before and before[relative].get("exists"):
                    tx.stage_bytes(relative, base64.b64decode(before[relative]["content_b64"]))
                else:
                    tx.stage_delete(relative)
            committed = tx.commit({"migration_id": migration_id})
        record["status"] = "rolled_back"
        record["rolled_back_at"] = now_iso()
        record["rollback_transaction_id"] = committed.transaction_id
        from .utils import atomic_write
        atomic_write(record_path, json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return MigrationResult(migration_id, "rolled-back", plan, tuple(str(path) for path in committed.written_paths))

    def _v2_snapshot(self) -> list[dict[str, Any]]:
        roots = [self.vault / "06_Sources/Books", self.vault / "05_Knowledge/Skills", self.vault / "05_Knowledge/Graph"]
        items: list[dict[str, Any]] = []
        for root in roots:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file() and not self._is_v3_path(path):
                    items.append({"path": path.relative_to(self.vault).as_posix(), "sha256": sha256_file(path), "size": path.stat().st_size})
        return items

    @staticmethod
    def _load_json_optional(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _backup_mutable(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for relative in (REGISTRY_JSON, REGISTRY_MD):
            path = self.vault / relative
            result[relative.as_posix()] = {
                "exists": path.is_file(),
                "sha256": sha256_file(path) if path.is_file() else None,
                "content_b64": base64.b64encode(path.read_bytes()).decode("ascii") if path.is_file() else None,
            }
        return result

    def _hash_paths(self, relative_paths: list[str]) -> dict[str, str | None]:
        return {relative: sha256_file(self.vault / relative) if (self.vault / relative).is_file() else None for relative in relative_paths}

    @staticmethod
    def _legacy_name(name: str, registry: SkillRegistry) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "legacy-skill"
        base = f"review-legacy-{base}"[:63].rstrip("-")
        candidate = base
        index = 2
        while candidate in registry.by_name:
            candidate = f"{base[:59]}-{index}"
            index += 1
        return candidate

    @staticmethod
    def _is_v3_path(path: Path) -> bool:
        name = path.name.casefold()
        return "v3" in name or "synthesis_proposals" in name or "exposures" in name
