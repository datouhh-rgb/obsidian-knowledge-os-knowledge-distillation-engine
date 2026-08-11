from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import REGISTRY_JSON, REGISTRY_MD, SkillRegistry
from .transaction import VaultTransaction, remove_exposure_link
from .utils import now_iso, parse_frontmatter, sha256_file


EXPOSURE_JSON = Path("05_Knowledge/Skills/EXPOSURES.json")
AUDIT_ROOT = Path("05_Knowledge/Audit/Events")


@dataclass(frozen=True)
class ExposureResult:
    action: str
    skill_name: str
    status: str
    link_path: str | None
    target_path: str
    event_id: str | None
    issues: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "skill": self.skill_name,
            "status": self.status,
            "link_path": self.link_path,
            "target_path": self.target_path,
            "event_id": self.event_id,
            "issues": list(self.issues),
        }


class SkillExposureManager:
    def __init__(self, vault: Path, user_agents_root: Path | None = None) -> None:
        self.vault = vault.resolve()
        if not (self.vault / "05_Knowledge/Skills").is_dir():
            raise ValueError("Target is not an existing Knowledge OS Vault.")
        self.user_agents_root = (user_agents_root or (Path.home() / ".agents" / "skills")).absolute()

    def expose(
        self,
        skill_name: str,
        scope: str,
        repo: Path | None = None,
        idempotency_key: str | None = None,
    ) -> ExposureResult:
        if scope not in {"user", "repo"}:
            raise ValueError("scope must be user or repo.")
        payload = {
            "skill": skill_name,
            "scope": scope,
            "repo": None if repo is None else str(repo.resolve()),
        }
        with VaultTransaction(
            self.vault, "skill-expose", idempotency_key=idempotency_key, request_payload=payload,
        ) as tx:
            exposure = self._load_exposure()
            registry = SkillRegistry.load(self.vault)
            try:
                record, version_record, lifecycle = registry.version(skill_name)
            except KeyError as exc:
                raise ValueError("Skill candidate has not been materialized as an approvable version.") from exc
            if lifecycle.get("status") != "approved":
                raise ValueError("Only an approved exact Skill version may be exposed.")
            self._validate_skill_artifacts(record, version_record)
            link = self._link_path(skill_name, scope, repo)
            target = self._skill_target(skill_name)
            existing = exposure["skills"].get(skill_name)
            if existing and (
                existing.get("scope") != scope
                or Path(existing.get("link_path", "")).absolute() != link
            ):
                raise ValueError("检测到 repo/user scope 重复暴露冲突。")
            if os.path.lexists(str(link)) and (
                not VaultTransaction._is_link_like(link) or link.resolve() != target.resolve()
            ):
                raise ValueError("Exposure 冲突：路径是实体或指向错误目标。")
            if tx.duplicate:
                prior = tx.prior_event or {}
                return ExposureResult(
                    "expose", skill_name, "duplicate", str(link), str(target), str(prior.get("event_id")),
                )
            exposure["skills"][skill_name] = {
                "skill_id": record["skill_id"],
                "version": record["current_version"],
                "artifact_sha256": version_record["artifact_sha256"],
                "scope": scope,
                "repo": None if repo is None else str(repo.resolve()),
                "link_path": str(link),
                "target_path": str(target),
                "status": "exposed",
                "event_id": tx.event_id,
                "updated_at": now_iso(),
            }
            exposure["updated_at"] = now_iso()
            lifecycle["exposures"] = [exposure["skills"][skill_name]]
            agent_path = target / "agents/openai.yaml"
            tx.stage_link_create(link, target)
            tx.stage_text(EXPOSURE_JSON, json.dumps(exposure, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            tx.stage_text(REGISTRY_JSON, registry.to_json())
            tx.stage_text(REGISTRY_MD, registry.to_markdown())
            tx.stage_text(agent_path.relative_to(self.vault), self._set_implicit(agent_path.read_text(encoding="utf-8"), True))
            tx.stage_text(
                AUDIT_ROOT / f"{tx.event_id}.json",
                self._audit(tx.event_id, "expose", skill_name, record["current_version"], scope, link, target),
            )
            result = tx.commit({"skill": skill_name, "scope": scope, "link": str(link)})
            return ExposureResult("expose", skill_name, "exposed", str(link), str(target), result.event_id)

    def unexpose(self, skill_name: str, idempotency_key: str | None = None) -> ExposureResult:
        with VaultTransaction(
            self.vault, "skill-unexpose", idempotency_key=idempotency_key,
            request_payload={"skill": skill_name},
        ) as tx:
            exposure = self._load_exposure()
            registry = SkillRegistry.load(self.vault)
            current = exposure["skills"].get(skill_name)
            target = self._skill_target(skill_name)
            if current is None:
                return ExposureResult("unexpose", skill_name, "not-exposed", None, str(target), None)
            link = Path(current["link_path"])
            if os.path.lexists(str(link)) and not VaultTransaction._is_link_like(link):
                raise ValueError("Exposure path was manually replaced by an entity; refusing deletion.")
            if tx.duplicate:
                prior = tx.prior_event or {}
                return ExposureResult(
                    "unexpose", skill_name, "duplicate", str(link), str(target), str(prior.get("event_id")),
                )
            exposure["skills"].pop(skill_name)
            exposure["updated_at"] = now_iso()
            try:
                record, _, lifecycle = registry.version(skill_name)
                lifecycle["exposures"] = []
                version = record["current_version"]
            except KeyError:
                version = str(current.get("version", "unknown"))
            tx.stage_link_remove(link)
            tx.stage_text(EXPOSURE_JSON, json.dumps(exposure, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            tx.stage_text(REGISTRY_JSON, registry.to_json())
            tx.stage_text(REGISTRY_MD, registry.to_markdown())
            agent_path = target / "agents/openai.yaml"
            if agent_path.is_file():
                tx.stage_text(agent_path.relative_to(self.vault), self._set_implicit(agent_path.read_text(encoding="utf-8"), False))
            tx.stage_text(
                AUDIT_ROOT / f"{tx.event_id}.json",
                self._audit(tx.event_id, "unexpose", skill_name, version, str(current.get("scope")), link, target),
            )
            result = tx.commit({"skill": skill_name, "link": str(link)})
            return ExposureResult("unexpose", skill_name, "unexposed", str(link), str(target), result.event_id)

    def status(self, skill_name: str) -> ExposureResult:
        exposure = self._load_exposure()
        target = self._skill_target(skill_name)
        record = exposure["skills"].get(skill_name)
        issues: list[dict[str, Any]] = []
        if record is None:
            return ExposureResult("status", skill_name, "not-exposed", None, str(target), None)
        link = Path(record["link_path"])
        if not os.path.lexists(str(link)):
            issues.append({"code": "broken-link", "message": "Exposure Registry exists but the link is missing."})
        elif not VaultTransaction._is_link_like(link):
            issues.append({"code": "manual-replacement", "message": "Exposure path is not a link/junction."})
        elif link.resolve() != target.resolve():
            issues.append({"code": "wrong-target", "message": f"Link points to {link.resolve()}, expected {target}."})
        try:
            registry = SkillRegistry.load(self.vault)
            skill_record, version_record, lifecycle = registry.version(skill_name, str(record["version"]))
            if lifecycle.get("status") != "approved":
                issues.append({"code": "not-approved", "message": "Exposed version is no longer approved."})
            self._validate_skill_artifacts(skill_record, version_record)
            if record.get("artifact_sha256") != version_record.get("artifact_sha256"):
                issues.append({"code": "artifact-mismatch", "message": "Exposure binds a different artifact hash."})
        except (KeyError, ValueError) as exc:
            issues.append({"code": "registry-mismatch", "message": str(exc)})
        return ExposureResult(
            "status", skill_name, "healthy" if not issues else "invalid", str(link), str(target),
            str(record.get("event_id")), tuple(issues),
        )

    def reconcile(self, repair: bool = False) -> dict[str, Any]:
        exposure = self._load_exposure()
        results = [self.status(name).as_dict() for name in sorted(exposure["skills"])]
        issues = [issue for item in results for issue in item["issues"]]
        event_id: str | None = None
        if repair and issues:
            removable = {
                item["skill"] for item in results
                if item["status"] == "invalid" and all(issue["code"] == "broken-link" for issue in item["issues"])
            }
            if removable:
                with VaultTransaction(
                    self.vault, "skill-reconcile", request_payload={"removed": sorted(removable)},
                ) as tx:
                    for name in removable:
                        exposure["skills"].pop(name, None)
                    exposure["updated_at"] = now_iso()
                    event_id = tx.event_id
                    tx.stage_text(EXPOSURE_JSON, json.dumps(exposure, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                    tx.stage_text(
                        AUDIT_ROOT / f"{event_id}.json",
                        json.dumps({
                            "event_id": event_id, "action": "reconcile", "removed": sorted(removable),
                            "created_at": now_iso(),
                        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    )
                    tx.commit({"removed": sorted(removable)})
        return {
            "status": "passed" if not issues else "issues",
            "results": results,
            "issue_count": len(issues),
            "event_id": event_id,
        }

    def test_trigger(self, skill_name: str) -> dict[str, Any]:
        del skill_name
        raise ValueError(
            "Synthetic test-trigger was removed in V3.1. Use generate-trigger-cases, "
            "run-trigger-eval, then record-eval with raw evaluator provenance."
        )

    def _link_path(self, skill_name: str, scope: str, repo: Path | None) -> Path:
        if scope == "user":
            return self.user_agents_root / skill_name
        if repo is None:
            raise ValueError("repo scope requires --repo.")
        repo = repo.resolve()
        if not repo.is_dir():
            raise ValueError(f"Repo directory does not exist: {repo}")
        return repo / ".agents" / "skills" / skill_name

    def _skill_target(self, skill_name: str) -> Path:
        target = (self.vault / "05_Knowledge/Skills" / skill_name).resolve()
        try:
            target.relative_to(self.vault)
        except ValueError as exc:
            raise ValueError("Skill name escapes the Vault path.") from exc
        if not target.is_dir():
            raise ValueError(f"Vault Skill directory does not exist: {target}")
        return target

    def _validate_skill_artifacts(self, record: dict[str, Any], version_record: dict[str, Any]) -> None:
        root = self._skill_target(str(record["canonical_name"]))
        version_skill = self.vault / version_record["artifact_path"]
        root_skill = root / "SKILL.md"
        version_root = version_skill.parent
        version_json = version_root / "VERSION.json"
        if not version_skill.is_file() or not root_skill.is_file() or not version_json.is_file():
            raise ValueError("Skill version/root materialization is incomplete.")
        expected = version_record.get("artifact_sha256")
        if not expected or sha256_file(version_skill) != expected or sha256_file(root_skill) != expected:
            raise ValueError("Skill file hash does not match the immutable version record.")
        version_hash = version_record.get("version_record_sha256")
        if version_hash and sha256_file(version_json) != version_hash:
            raise ValueError("VERSION.json hash does not match the immutable version record.")
        frontmatter, _ = parse_frontmatter(root_skill.read_text(encoding="utf-8"))
        if frontmatter.get("name") != record["canonical_name"]:
            raise ValueError("SKILL.md name differs from Registry canonical_name.")
        description = str(frontmatter.get("description", ""))
        has_when = bool(re.search(r"(?:use when|何时|当.+时)", description, flags=re.IGNORECASE))
        has_not = bool(re.search(r"(?:do not use|不要|禁止|不适用)", description, flags=re.IGNORECASE))
        if not has_when or not has_not:
            raise ValueError("Skill description must explain what it does, when to use it, and when not to use it.")

    def _load_exposure(self) -> dict[str, Any]:
        path = self.vault / EXPOSURE_JSON
        if not path.is_file():
            return {"schema_version": 3, "engine_version": "3.2.0", "updated_at": None, "skills": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Exposure Registry cannot be read: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
            raise ValueError("Exposure Registry structure is invalid.")
        return data

    @staticmethod
    def _set_implicit(text: str, value: bool) -> str:
        desired = "true" if value else "false"
        if re.search(r"allow_implicit_invocation:\s*(?:true|false)", text):
            return re.sub(
                r"allow_implicit_invocation:\s*(?:true|false)",
                f"allow_implicit_invocation: {desired}", text,
            )
        return text.rstrip() + f"\npolicy:\n  allow_implicit_invocation: {desired}\n"

    @staticmethod
    def _audit(
        event_id: str,
        action: str,
        skill: str,
        version: str,
        scope: str,
        link: Path,
        target: Path,
    ) -> str:
        return json.dumps({
            "event_id": event_id,
            "action": action,
            "skill": skill,
            "version": version,
            "scope": scope,
            "link_path": str(link),
            "target_path": str(target),
            "created_at": now_iso(),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
