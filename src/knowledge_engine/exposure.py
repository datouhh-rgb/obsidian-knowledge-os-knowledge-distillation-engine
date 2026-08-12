from __future__ import annotations

import json
import os
import re
import shutil
import stat
import uuid
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
    deployment_mode: str = "symlink"

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "skill": self.skill_name,
            "status": self.status,
            "link_path": self.link_path,
            "target_path": self.target_path,
            "event_id": self.event_id,
            "issues": list(self.issues),
            "deployment_mode": self.deployment_mode,
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
            deployment_mode = str(
                existing.get("deployment_mode") if existing else self._preferred_deployment_mode(link)
            )
            if existing and (
                existing.get("scope") != scope
                or Path(existing.get("link_path", "")).absolute() != link
            ):
                raise ValueError("检测到 repo/user scope 重复暴露冲突。")
            if deployment_mode == "symlink":
                if os.path.lexists(str(link)) and (
                    not link.is_symlink() or link.resolve() != target.resolve()
                ):
                    raise ValueError("Exposure 冲突：路径不是指向 Vault Skill 的真实符号链接。")
            elif deployment_mode == "managed-cache":
                if os.path.lexists(str(link)) and not self._managed_cache_matches(
                    link, record, version_record, require_read_only=True,
                )[0]:
                    raise ValueError("Managed Deployment Cache drift/conflict detected; reconcile --repair or unexpose first.")
            else:
                raise ValueError(f"Unsupported deployment mode: {deployment_mode}")
            if tx.duplicate:
                prior = tx.prior_event or {}
                return ExposureResult(
                    "expose", skill_name, "duplicate", str(link), str(target), str(prior.get("event_id")),
                    deployment_mode=deployment_mode,
                )
            exposure["skills"][skill_name] = {
                "skill_id": record["skill_id"],
                "version": record["current_version"],
                "artifact_sha256": version_record["artifact_sha256"],
                "scope": scope,
                "repo": None if repo is None else str(repo.resolve()),
                "link_path": str(link),
                "target_path": str(target),
                "deployment_mode": deployment_mode,
                "deployment_manifest_path": str(link / "DEPLOYMENT_MANIFEST.json") if deployment_mode == "managed-cache" else None,
                "status": "exposed",
                "event_id": tx.event_id,
                "updated_at": now_iso(),
            }
            exposure["updated_at"] = now_iso()
            lifecycle["exposures"] = [exposure["skills"][skill_name]]
            agent_path = target / "agents/openai.yaml"
            created_cache = False
            if deployment_mode == "symlink":
                tx.stage_link_create(link, target)
            else:
                created_cache = self._deploy_managed_cache(link, record, version_record)
            tx.stage_text(EXPOSURE_JSON, json.dumps(exposure, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            tx.stage_text(REGISTRY_JSON, registry.to_json())
            tx.stage_text(REGISTRY_MD, registry.to_markdown())
            # Exposure makes an approved Skill discoverable, but V3.3 production
            # acceptance keeps implicit invocation disabled. Explicit `$skill`
            # invocation remains available through Codex's standard policy.
            tx.stage_text(agent_path.relative_to(self.vault), self._set_implicit(agent_path.read_text(encoding="utf-8"), False))
            tx.stage_text(
                AUDIT_ROOT / f"{tx.event_id}.json",
                self._audit(tx.event_id, "expose", skill_name, record["current_version"], scope, link, target, deployment_mode),
            )
            try:
                result = tx.commit({
                    "skill": skill_name, "scope": scope, "link": str(link),
                    "deployment_mode": deployment_mode,
                })
            except Exception:
                if deployment_mode == "managed-cache" and created_cache and os.path.lexists(str(link)):
                    self._remove_managed_cache(link, require_owned=False)
                raise
            return ExposureResult(
                "expose", skill_name, "exposed", str(link), str(target), result.event_id,
                deployment_mode=deployment_mode,
            )

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
            deployment_mode = str(current.get("deployment_mode", "symlink"))
            if deployment_mode == "symlink":
                if os.path.lexists(str(link)) and not link.is_symlink():
                    raise ValueError("Exposure path was manually replaced by an entity; refusing deletion.")
            elif deployment_mode == "managed-cache":
                record, version_record, _ = registry.version(skill_name, str(current.get("version")))
                matches, issues = self._managed_cache_matches(link, record, version_record, require_read_only=False)
                if os.path.lexists(str(link)) and not matches:
                    raise ValueError("Managed Deployment Cache drift detected; refusing deletion: " + "; ".join(issues))
            else:
                raise ValueError(f"Unsupported deployment mode: {deployment_mode}")
            if tx.duplicate:
                prior = tx.prior_event or {}
                return ExposureResult(
                    "unexpose", skill_name, "duplicate", str(link), str(target), str(prior.get("event_id")),
                    deployment_mode=deployment_mode,
                )
            exposure["skills"].pop(skill_name)
            exposure["updated_at"] = now_iso()
            try:
                record, _, lifecycle = registry.version(skill_name)
                lifecycle["exposures"] = []
                version = record["current_version"]
            except KeyError:
                version = str(current.get("version", "unknown"))
            if deployment_mode == "symlink":
                tx.stage_link_remove(link)
            tx.stage_text(EXPOSURE_JSON, json.dumps(exposure, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            tx.stage_text(REGISTRY_JSON, registry.to_json())
            tx.stage_text(REGISTRY_MD, registry.to_markdown())
            agent_path = target / "agents/openai.yaml"
            if agent_path.is_file():
                tx.stage_text(agent_path.relative_to(self.vault), self._set_implicit(agent_path.read_text(encoding="utf-8"), False))
            tx.stage_text(
                AUDIT_ROOT / f"{tx.event_id}.json",
                self._audit(
                    tx.event_id, "unexpose", skill_name, version,
                    str(current.get("scope")), link, target, deployment_mode,
                ),
            )
            result = tx.commit({"skill": skill_name, "link": str(link)})
            if deployment_mode == "managed-cache" and os.path.lexists(str(link)):
                self._remove_managed_cache(link, require_owned=True)
            return ExposureResult(
                "unexpose", skill_name, "unexposed", str(link), str(target), result.event_id,
                deployment_mode=deployment_mode,
            )

    def status(self, skill_name: str) -> ExposureResult:
        exposure = self._load_exposure()
        target = self._skill_target(skill_name)
        record = exposure["skills"].get(skill_name)
        issues: list[dict[str, Any]] = []
        if record is None:
            return ExposureResult("status", skill_name, "not-exposed", None, str(target), None)
        link = Path(record["link_path"])
        deployment_mode = str(record.get("deployment_mode", "symlink"))
        if not os.path.lexists(str(link)):
            issues.append({"code": "missing-deployment", "message": "Exposure Registry exists but deployment is missing."})
        elif deployment_mode == "symlink":
            if not link.is_symlink():
                issues.append({"code": "manual-replacement", "message": "Exposure path is not a true directory symlink."})
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
            if deployment_mode == "managed-cache" and os.path.lexists(str(link)):
                matches, cache_issues = self._managed_cache_matches(
                    link, skill_record, version_record, require_read_only=True,
                )
                if not matches:
                    issues.extend({"code": "deployment-drift", "message": item} for item in cache_issues)
            elif deployment_mode not in {"symlink", "managed-cache"}:
                issues.append({"code": "deployment-mode", "message": f"Unsupported deployment mode: {deployment_mode}"})
        except (KeyError, ValueError) as exc:
            issues.append({"code": "registry-mismatch", "message": str(exc)})
        return ExposureResult(
            "status", skill_name, "healthy" if not issues else "invalid", str(link), str(target),
            str(record.get("event_id")), tuple(issues), deployment_mode,
        )

    def reconcile(self, repair: bool = False) -> dict[str, Any]:
        exposure = self._load_exposure()
        results = [self.status(name).as_dict() for name in sorted(exposure["skills"])]
        repair_actions: list[dict[str, Any]] = []
        if repair:
            registry = SkillRegistry.load(self.vault)
            for item in results:
                if item["status"] != "invalid" or item.get("deployment_mode") != "managed-cache":
                    continue
                record = exposure["skills"].get(item["skill"], {})
                link = Path(str(record.get("link_path", "")))
                try:
                    skill_record, version_record, _ = registry.version(item["skill"], str(record.get("version")))
                    if os.path.lexists(str(link)):
                        self._remove_managed_cache(link, require_owned=True)
                    self._deploy_managed_cache(link, skill_record, version_record)
                    repair_actions.append({"skill": item["skill"], "action": "rebuild-managed-cache"})
                except (OSError, ValueError, KeyError) as exc:
                    repair_actions.append({"skill": item["skill"], "action": "blocked", "reason": str(exc)})
            results = [self.status(name).as_dict() for name in sorted(exposure["skills"])]
        orphans = self._managed_cache_orphans(exposure)
        if repair:
            for path in list(orphans):
                try:
                    self._remove_managed_cache(path, require_owned=True)
                    repair_actions.append({"path": str(path), "action": "remove-orphan-managed-cache"})
                    orphans.remove(path)
                except (OSError, ValueError) as exc:
                    repair_actions.append({"path": str(path), "action": "blocked", "reason": str(exc)})
        issues = [issue for item in results for issue in item["issues"]]
        issues.extend({"code": "orphan-managed-cache", "message": str(path)} for path in orphans)
        event_id: str | None = None
        if repair and (issues or repair_actions):
            removable = {
                item["skill"] for item in results
                if item["status"] == "invalid"
                and item.get("deployment_mode") == "symlink"
                and all(issue["code"] == "missing-deployment" for issue in item["issues"])
            }
            if removable or repair_actions:
                with VaultTransaction(
                    self.vault, "skill-reconcile",
                    request_payload={"removed": sorted(removable), "repair_actions": repair_actions},
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
                            "repair_actions": repair_actions,
                            "created_at": now_iso(),
                        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    )
                    tx.commit({"removed": sorted(removable), "repair_actions": repair_actions})
        return {
            "status": "passed" if not issues else "issues",
            "results": results,
            "issue_count": len(issues),
            "event_id": event_id,
            "orphan_managed_caches": [str(path) for path in orphans],
            "repair_actions": repair_actions,
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

    def _preferred_deployment_mode(self, link: Path) -> str:
        if os.name != "nt":
            return "symlink"
        return "symlink" if self._supports_true_symlink(link.parent) else "managed-cache"

    @staticmethod
    def _supports_true_symlink(parent: Path) -> bool:
        parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        target = parent / f".symlink-capability-target-{token}"
        link = parent / f".symlink-capability-link-{token}"
        try:
            target.mkdir()
            os.symlink(target, link, target_is_directory=True)
            return link.is_symlink() and link.resolve() == target.resolve()
        except OSError:
            return False
        finally:
            if link.is_symlink():
                link.unlink()
            if target.is_dir():
                target.rmdir()

    def _version_root(self, version_record: dict[str, Any]) -> Path:
        source = (self.vault / str(version_record["artifact_path"])).resolve().parent
        try:
            source.relative_to(self.vault)
        except ValueError as exc:
            raise ValueError("Version artifact escapes the authoritative Vault.") from exc
        if not source.is_dir():
            raise ValueError("Approved immutable version directory is missing.")
        return source

    @staticmethod
    def _artifact_hashes(root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): sha256_file(path)
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.name != "DEPLOYMENT_MANIFEST.json"
        }

    def _deployment_manifest(
        self,
        target: Path,
        source: Path,
        record: dict[str, Any],
        version_record: dict[str, Any],
        deployed: dict[str, str],
    ) -> dict[str, Any]:
        source_hashes = self._artifact_hashes(source)
        return {
            "schema_version": 1,
            "deployment_mode": "managed-cache",
            "authoritative_vault": str(self.vault),
            "source_version_path": str(source),
            "target_path": str(target.absolute()),
            "skill_id": record["skill_id"],
            "skill_name": record["canonical_name"],
            "version": version_record["version"],
            "source_skill_sha256": version_record["artifact_sha256"],
            "deployed_skill_sha256": deployed.get("SKILL.md", ""),
            "source_artifacts": source_hashes,
            "deployed_artifacts": deployed,
            "deployed_at": now_iso(),
            "immutable": True,
            "read_only": True,
            "reverse_sync": False,
        }

    def _deploy_managed_cache(
        self,
        target: Path,
        record: dict[str, Any],
        version_record: dict[str, Any],
    ) -> bool:
        if os.path.lexists(str(target)):
            matches, issues = self._managed_cache_matches(target, record, version_record, require_read_only=True)
            if matches:
                return False
            raise ValueError("Managed cache path already exists with drift: " + "; ".join(issues))
        source = self._version_root(version_record)
        policy_path = source / "agents/openai.yaml"
        if not policy_path.is_file() or not re.search(
            r"allow_implicit_invocation:\s*false", policy_path.read_text(encoding="utf-8"), re.IGNORECASE,
        ):
            raise ValueError("Approved version must disable implicit invocation before managed deployment.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.deploy-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source, temporary, copy_function=shutil.copy2)
            deployed = self._artifact_hashes(temporary)
            source_hashes = self._artifact_hashes(source)
            if deployed != source_hashes:
                raise ValueError("Managed deployment bytes differ from the approved immutable version.")
            manifest = self._deployment_manifest(target, source, record, version_record, deployed)
            (temporary / "DEPLOYMENT_MANIFEST.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8", newline="\n",
            )
            for path in temporary.rglob("*"):
                if path.is_file():
                    path.chmod(stat.S_IREAD)
            temporary.replace(target)
            return True
        except Exception:
            if temporary.exists():
                self._make_tree_writable(temporary)
                shutil.rmtree(temporary)
            raise

    def _managed_cache_matches(
        self,
        target: Path,
        record: dict[str, Any],
        version_record: dict[str, Any],
        *,
        require_read_only: bool,
    ) -> tuple[bool, list[str]]:
        issues: list[str] = []
        manifest_path = target / "DEPLOYMENT_MANIFEST.json"
        if not target.is_dir() or not manifest_path.is_file():
            return False, ["deployment directory or DEPLOYMENT_MANIFEST.json is missing"]
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return False, [f"deployment manifest is unreadable: {exc}"]
        expected = {
            "deployment_mode": "managed-cache",
            "authoritative_vault": str(self.vault),
            "target_path": str(target.absolute()),
            "skill_id": record["skill_id"],
            "skill_name": record["canonical_name"],
            "version": version_record["version"],
            "source_skill_sha256": version_record["artifact_sha256"],
            "immutable": True,
            "read_only": True,
            "reverse_sync": False,
        }
        for key, value in expected.items():
            if payload.get(key) != value:
                issues.append(f"manifest {key} mismatch")
        deployed = self._artifact_hashes(target)
        source = self._version_root(version_record)
        source_hashes = self._artifact_hashes(source)
        if payload.get("source_artifacts") != source_hashes:
            issues.append("source artifact hashes drifted from the approved version")
        if payload.get("deployed_artifacts") != deployed or deployed != source_hashes:
            issues.append("deployed artifact hash drift detected")
        if deployed.get("SKILL.md") != version_record.get("artifact_sha256"):
            issues.append("deployed SKILL.md hash differs from the sealed version")
        if require_read_only:
            writable = [
                path.relative_to(target).as_posix() for path in target.rglob("*")
                if path.is_file() and path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
            ]
            if writable:
                issues.append("deployment contains writable files: " + ", ".join(writable))
        return not issues, issues

    def _remove_managed_cache(self, target: Path, *, require_owned: bool) -> None:
        if not os.path.lexists(str(target)):
            return
        if not target.is_dir() or target.is_symlink():
            raise ValueError("Refusing to remove a non-cache deployment path.")
        if require_owned:
            manifest_path = target / "DEPLOYMENT_MANIFEST.json"
            if not manifest_path.is_file():
                raise ValueError("Refusing to remove a cache without its ownership manifest.")
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            source = Path(str(payload.get("source_version_path", ""))).resolve()
            try:
                source.relative_to(self.vault)
            except ValueError as exc:
                raise ValueError("Deployment manifest does not bind this Vault.") from exc
            if payload.get("authoritative_vault") != str(self.vault) or payload.get("target_path") != str(target.absolute()):
                raise ValueError("Deployment ownership manifest does not match this target.")
        self._make_tree_writable(target)
        shutil.rmtree(target)

    @staticmethod
    def _make_tree_writable(root: Path) -> None:
        for path in sorted(root.rglob("*"), reverse=True):
            try:
                path.chmod(stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        try:
            root.chmod(stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass

    def _managed_cache_orphans(self, exposure: dict[str, Any]) -> list[Path]:
        if not self.user_agents_root.is_dir():
            return []
        registered = {
            str(Path(item.get("link_path", "")).absolute())
            for item in exposure.get("skills", {}).values()
            if item.get("deployment_mode") == "managed-cache"
        }
        orphans: list[Path] = []
        for manifest_path in self.user_agents_root.glob("*/DEPLOYMENT_MANIFEST.json"):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            target = manifest_path.parent
            if (
                payload.get("deployment_mode") == "managed-cache"
                and payload.get("authoritative_vault") == str(self.vault)
                and str(target.absolute()) not in registered
            ):
                orphans.append(target)
        return sorted(orphans)

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
        deployment_mode: str,
    ) -> str:
        return json.dumps({
            "event_id": event_id,
            "action": action,
            "skill": skill,
            "version": version,
            "scope": scope,
            "link_path": str(link),
            "target_path": str(target),
            "deployment_mode": deployment_mode,
            "created_at": now_iso(),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
