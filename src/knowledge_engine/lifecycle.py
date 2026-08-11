from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exposure import AUDIT_ROOT, EXPOSURE_JSON
from .graph import GRAPH_JSON, GRAPH_MD, ConceptGraph
from .registry import REGISTRY_JSON, REGISTRY_MD, SkillRegistry
from .transaction import CommitResult, VaultTransaction
from .trigger_eval import TriggerEvalValidator
from .utils import now_iso, safe_filename, sha256_file, wikilink, yaml_frontmatter


@dataclass(frozen=True)
class LifecycleResult:
    skill_name: str
    version: str
    project: str | None
    action: str
    written_paths: tuple[Path, ...]
    record: dict[str, Any]
    event_id: str | None
    duplicate: bool = False


class SkillLifecycleService:
    def __init__(self, vault: Path) -> None:
        self.vault = vault.resolve()
        if not (self.vault / "05_Knowledge").is_dir() or not (self.vault / "06_Sources").is_dir():
            raise ValueError("目标不是现有 Knowledge OS Vault。")

    def link_project(self, skill_name: str, project: str, version: str | None = None, idempotency_key: str | None = None) -> LifecycleResult:
        with VaultTransaction(
            self.vault, "link-project", idempotency_key=idempotency_key,
            request_payload={"skill": skill_name, "version": version, "project": project},
        ) as tx:
            if tx.duplicate:
                prior = tx.prior_event or {}
                registry = SkillRegistry.load(self.vault)
                record, _, _ = registry.version(skill_name, version)
                return LifecycleResult(skill_name, version or record["current_version"], project, "link-project", (), record, str(prior.get("event_id")), True)
            registry = SkillRegistry.load(self.vault)
            graph = ConceptGraph.load(self.vault)
            record, _, lifecycle = registry.version(skill_name, version)
            selected = version or record["current_version"]
            projects = lifecycle["usage"].setdefault("projects", [])
            if project not in projects:
                projects.append(project)
                projects.sort()
            graph.link_project(record["skill_id"], record["canonical_name"], record["title"], selected, project, tx.event_id)
            self._stage_core(tx, registry, graph)
            tx.stage_text(AUDIT_ROOT / f"{tx.event_id}.json", self._audit(tx.event_id, "link-project", record, selected, {"project": project}))
            committed = tx.commit({"skill": skill_name, "version": selected, "project": project})
            return LifecycleResult(skill_name, selected, project, "link-project", committed.written_paths, record, committed.event_id)

    def record_use(
        self,
        skill_name: str,
        project: str,
        outcome: str,
        feedback: str | None = None,
        version: str | None = None,
        idempotency_key: str | None = None,
        fail_after: int | None = None,
        crash_after: int | None = None,
    ) -> LifecycleResult:
        with VaultTransaction(
            self.vault, "record-use", idempotency_key=idempotency_key,
            request_payload={
                "skill": skill_name, "version": version, "project": project,
                "outcome": outcome, "feedback": feedback or "",
            },
            fail_after=fail_after, crash_after=crash_after,
        ) as tx:
            registry = SkillRegistry.load(self.vault)
            record, _, _ = registry.version(skill_name, version)
            selected = version or record["current_version"]
            if tx.duplicate:
                prior = tx.prior_event or {}
                return LifecycleResult(skill_name, selected, project, "record-use", (), record, str(prior.get("event_id")), True)
            self._check_exposure_version(skill_name, selected)
            graph = ConceptGraph.load(self.vault)
            event = registry.record_use(skill_name, selected, project, outcome, feedback, tx.event_id)
            graph.link_project(record["skill_id"], record["canonical_name"], record["title"], selected, project, tx.event_id)
            review_path = self._event_path(skill_name, selected, tx.event_id)
            self._stage_core(tx, registry, graph)
            tx.stage_text(review_path, self._event_note(event, record))
            tx.stage_text(AUDIT_ROOT / f"{tx.event_id}.json", self._audit(tx.event_id, "record-use", record, selected, event))
            committed = tx.commit({"skill": skill_name, "version": selected, "project": project, "outcome": outcome})
            return LifecycleResult(skill_name, selected, project, "record-use", committed.written_paths, record, committed.event_id)

    def record_eval(
        self,
        skill_name: str,
        version: str,
        eval_payload: dict[str, Any],
        evaluation_path: Path | str | None,
    ) -> LifecycleResult:
        if evaluation_path is None:
            raise ValueError("record-eval requires the immutable real evaluation artifact path.")
        supplied = Path(evaluation_path)
        absolute = supplied.resolve() if supplied.is_absolute() else (self.vault / supplied).resolve()
        try:
            relative = absolute.relative_to(self.vault)
        except ValueError as exc:
            raise ValueError("Evaluation artifact must be stored inside the existing Vault.") from exc
        if not absolute.is_file():
            raise ValueError("Evaluation artifact does not exist in the Vault.")
        on_disk = json.loads(absolute.read_text(encoding="utf-8"))
        if on_disk != eval_payload:
            raise ValueError("Provided evaluation payload differs from its immutable Vault artifact.")
        TriggerEvalValidator.validate_run(self.vault, eval_payload, skill_name, version)
        with VaultTransaction(
            self.vault, "record-eval",
            request_payload={
                "skill": skill_name, "version": version,
                "run_id": eval_payload.get("run_id"), "artifact": eval_payload.get("artifact_sha256"),
            },
        ) as tx:
            registry = SkillRegistry.load(self.vault)
            record = registry.require(skill_name)
            evaluation = registry.record_eval(skill_name, version, eval_payload, relative.as_posix())
            tx.stage_text(REGISTRY_JSON, registry.to_json())
            tx.stage_text(REGISTRY_MD, registry.to_markdown())
            tx.stage_text(AUDIT_ROOT / f"{tx.event_id}.json", self._audit(tx.event_id, "record-eval", record, version, {"eval_id": evaluation["eval_id"], "passed": evaluation["passed"]}))
            committed = tx.commit({"skill": skill_name, "version": version, "eval": evaluation["eval_id"]})
            return LifecycleResult(skill_name, version, None, "record-eval", committed.written_paths, record, committed.event_id)

    def semantic_review(
        self,
        skill_name: str,
        version: str,
        review_payload: dict[str, Any],
        reviewer: str,
    ) -> LifecycleResult:
        allowed = {"statement_hash", "decision", "reason", "evidence_ids"}
        if set(review_payload) != allowed:
            raise ValueError("semantic-review JSON requires exactly statement_hash, decision, reason, and evidence_ids.")
        decision = str(review_payload["decision"])
        reason = str(review_payload["reason"]).strip()
        evidence_ids = [str(item) for item in review_payload["evidence_ids"]]
        if decision not in {"supported", "unsupported"} or not reason or not evidence_ids:
            raise ValueError("semantic-review requires a supported/unsupported decision, reason, and Evidence IDs.")
        with VaultTransaction(
            self.vault, "semantic-review",
            request_payload={"skill": skill_name, "version": version, "review": review_payload, "reviewer": reviewer},
        ) as tx:
            registry = SkillRegistry.load(self.vault)
            record, version_record, _ = registry.version(skill_name, version)
            artifact = self.vault / version_record["artifact_path"]
            expected_artifact = str(version_record.get("artifact_sha256", ""))
            if not expected_artifact or not artifact.is_file() or sha256_file(artifact) != expected_artifact:
                raise ValueError("Semantic review target is not the sealed exact artifact.")
            assessments = {item["statement_hash"]: item for item in registry.effective_assessments(skill_name, version)}
            statement_hash = str(review_payload["statement_hash"])
            if statement_hash not in assessments:
                raise ValueError("semantic-review statement hash is not part of this version.")
            available_evidence = set(version_record.get("evidence_bindings", []))
            if not set(evidence_ids).issubset(available_evidence):
                raise ValueError("semantic-review references Evidence outside this immutable version.")
            event = {
                "event_id": tx.event_id,
                "skill_id": record["skill_id"],
                "skill": record["canonical_name"],
                "version": version,
                "artifact_sha256": expected_artifact,
                "statement_hash": statement_hash,
                "old_status": assessments[statement_hash]["status"],
                "decision": decision,
                "reason": reason,
                "evidence_ids": evidence_ids,
                "reviewer": reviewer,
                "reviewed_at": now_iso(),
            }
            review_path = Path("10_Reviews/Semantic") / skill_name / version / f"{tx.event_id}.json"
            event["event_path"] = review_path.as_posix()
            registry.append_semantic_review(skill_name, version, event)
            tx.stage_text(review_path, json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            tx.stage_text(REGISTRY_JSON, registry.to_json())
            tx.stage_text(REGISTRY_MD, registry.to_markdown())
            tx.stage_text(AUDIT_ROOT / f"{tx.event_id}.json", self._audit(tx.event_id, "semantic-review", record, version, event))
            committed = tx.commit({"skill": skill_name, "version": version, "semantic_review": tx.event_id})
            return LifecycleResult(skill_name, version, None, "semantic-review", committed.written_paths, record, committed.event_id)

    def approve(self, skill_name: str, version: str, approver: str, reason: str) -> LifecycleResult:
        with VaultTransaction(self.vault, "approve") as tx:
            registry = SkillRegistry.load(self.vault)
            record = registry.require(skill_name)
            approval = registry.approve(skill_name, version, approver, reason)
            tx.stage_text(REGISTRY_JSON, registry.to_json())
            tx.stage_text(REGISTRY_MD, registry.to_markdown())
            tx.stage_text(AUDIT_ROOT / f"{tx.event_id}.json", self._audit(tx.event_id, "approve", record, version, approval))
            committed = tx.commit({"skill": skill_name, "version": version, "approval": approval["approval_id"]})
            return LifecycleResult(skill_name, version, None, "approve", committed.written_paths, record, committed.event_id)

    def reject(self, skill_name: str, version: str, reviewer: str, reason: str) -> LifecycleResult:
        return self._decision("reject", skill_name, version, reviewer, reason)

    def deprecate(self, skill_name: str, version: str, reviewer: str, reason: str) -> LifecycleResult:
        return self._decision("deprecate", skill_name, version, reviewer, reason)

    def rollback(self, skill_name: str, version: str, reviewer: str, reason: str) -> LifecycleResult:
        with VaultTransaction(self.vault, "rollback") as tx:
            self._require_unexposed(skill_name)
            registry = SkillRegistry.load(self.vault)
            record, version_record, _ = registry.version(skill_name, version)
            event = registry.rollback(skill_name, version, reviewer, reason)
            version_root = (self.vault / version_record["artifact_path"]).parent
            skill_root = self.vault / "05_Knowledge/Skills" / record["canonical_name"]
            for relative in (Path("SKILL.md"), Path("agents/openai.yaml"), Path("references/evidence.json")):
                source = version_root / relative
                if not source.is_file():
                    raise ValueError(f"不可变 version 文件缺失：{source}")
                tx.stage_bytes((skill_root / relative).relative_to(self.vault), source.read_bytes())
            tx.stage_text(REGISTRY_JSON, registry.to_json())
            tx.stage_text(REGISTRY_MD, registry.to_markdown())
            tx.stage_text(AUDIT_ROOT / f"{tx.event_id}.json", self._audit(tx.event_id, "rollback", record, version, event))
            committed = tx.commit({"skill": skill_name, "version": version})
            return LifecycleResult(skill_name, version, None, "rollback", committed.written_paths, record, committed.event_id)

    def compare(self, skill_name: str, left: str, right: str) -> str:
        return SkillRegistry.load(self.vault).compare(skill_name, left, right)

    def _decision(self, action: str, skill_name: str, version: str, reviewer: str, reason: str) -> LifecycleResult:
        with VaultTransaction(self.vault, action) as tx:
            self._require_unexposed(skill_name)
            registry = SkillRegistry.load(self.vault)
            record = registry.require(skill_name)
            if action == "reject":
                registry.reject(skill_name, version, reviewer, reason)
            else:
                registry.deprecate(skill_name, version, reviewer, reason)
            tx.stage_text(REGISTRY_JSON, registry.to_json())
            tx.stage_text(REGISTRY_MD, registry.to_markdown())
            tx.stage_text(AUDIT_ROOT / f"{tx.event_id}.json", self._audit(tx.event_id, action, record, version, {"reviewer": reviewer, "reason": reason}))
            committed = tx.commit({"skill": skill_name, "version": version})
            return LifecycleResult(skill_name, version, None, action, committed.written_paths, record, committed.event_id)

    @staticmethod
    def _stage_core(tx: VaultTransaction, registry: SkillRegistry, graph: ConceptGraph) -> None:
        tx.stage_text(REGISTRY_JSON, registry.to_json())
        tx.stage_text(REGISTRY_MD, registry.to_markdown())
        tx.stage_text(GRAPH_JSON, graph.to_json())
        tx.stage_text(GRAPH_MD, graph.to_markdown())

    def _check_exposure_version(self, skill_name: str, version: str) -> None:
        path = self.vault / EXPOSURE_JSON
        if not path.is_file():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        exposure = data.get("skills", {}).get(skill_name)
        if exposure and exposure.get("version") != version:
            raise ValueError("usage version 与当前 exposure version 不一致。")

    def _require_unexposed(self, skill_name: str) -> None:
        path = self.vault / EXPOSURE_JSON
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if skill_name in data.get("skills", {}):
                raise ValueError("Skill 已暴露；先 unexpose 再改变版本状态或 rollback。")

    @staticmethod
    def _event_path(skill_name: str, version: str, event_id: str) -> Path:
        return Path("10_Reviews/Skill-Usage") / safe_filename(skill_name) / version / f"{event_id}.md"

    @staticmethod
    def _event_note(event: dict[str, Any], record: dict[str, Any]) -> str:
        skill_path = Path("05_Knowledge/Skills") / record["canonical_name"] / "SKILL.md"
        project = str(event["project"])
        frontmatter = yaml_frontmatter({
            "id": event["event_id"], "title": f"{record['canonical_name']}@{event['version']} - {project}",
            "type": "review", "status": "completed", "created": event["created_at"],
            "source": [wikilink(skill_path, record["canonical_name"])], "source_type": "skill-usage",
            "related_projects": [project], "ai_generated": True, "confidence": "high",
        })
        return frontmatter + (
            f"# Skill 使用复盘\n\n- Skill: {wikilink(skill_path, record['canonical_name'])}\n"
            f"- Skill ID: `{record['skill_id']}`\n- Version: `{event['version']}`\n- Project: {project}\n"
            f"- Outcome: `{event['outcome']}`\n- Feedback: {event['feedback'] or '未提供'}\n\n"
            "## Knowledge Improvement\n\n本记录只形成改进提案输入，不自动修改或激活 Skill。\n"
        )

    @staticmethod
    def _audit(event_id: str, action: str, record: dict[str, Any], version: str, detail: dict[str, Any]) -> str:
        return json.dumps({
            "event_id": event_id, "action": action, "skill_id": record["skill_id"],
            "skill": record["canonical_name"], "version": version,
            "detail": detail, "created_at": now_iso(),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
