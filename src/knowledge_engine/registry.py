from __future__ import annotations

import difflib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from .models import (
    ConversionIdentity,
    IngestedSource,
    QualityScore,
    SemanticAssessment,
    SkillCandidate,
    SkillMaterialization,
)
from .quality import SkillQualityEvaluator
from .utils import ascii_slug, now_iso, wikilink


REGISTRY_JSON = Path("05_Knowledge/Skills/REGISTRY_V3.json")
REGISTRY_MD = Path("05_Knowledge/Skills/Skill-Registry-V3.md")
REAL_EVALUATORS = {"codex-cli-implicit", "codex-cli-explicit", "approved-external-router"}


@dataclass
class SkillRegistry:
    data: dict[str, Any]

    @classmethod
    def empty(cls) -> "SkillRegistry":
        return cls({
            "schema_version": 3,
            "engine_version": "3.3.0",
            "revision": 0,
            "updated_at": None,
            "skills": {},
            "name_index": {},
            "usage_events": {},
        })

    @classmethod
    def load(cls, vault: Path) -> "SkillRegistry":
        path = vault / REGISTRY_JSON
        if not path.is_file():
            return cls.empty()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Skill Registry cannot be read: {exc}") from exc
        errors = sorted(Draft202012Validator(cls.schema()).iter_errors(data), key=lambda item: list(item.path))
        if errors:
            raise ValueError(f"Skill Registry schema invalid at {errors[0].json_path}: {errors[0].message}")
        return cls(data)

    @property
    def records(self) -> dict[str, dict[str, Any]]:
        return self.data["skills"]

    @property
    def by_name(self) -> dict[str, str]:
        return self.data["name_index"]

    def require(self, identifier: str) -> dict[str, Any]:
        skill_id = self.by_name.get(identifier, identifier)
        if skill_id not in self.records:
            raise KeyError(f"Skill does not exist in Registry: {identifier}")
        return self.records[skill_id]

    def version(self, identifier: str, version: str | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        record = self.require(identifier)
        selected = version or str(record.get("current_version") or "")
        versions = record.get("versions", {})
        if selected not in versions:
            raise KeyError(f"Skill {identifier} has no version {selected}")
        lifecycle = record.setdefault("lifecycle", {}).setdefault(selected, self._empty_lifecycle())
        # Non-destructive defaults keep V3.0 registries readable during migration.
        lifecycle.setdefault("semantic_reviews", [])
        lifecycle.setdefault("unresolved_reviews", [])
        return record, versions[selected], lifecycle

    def create_source_candidate(
        self,
        candidate: SkillCandidate,
        source: IngestedSource,
        conversion: ConversionIdentity,
        book_title: str,
        score: QualityScore,
        assessments: Iterable[SemanticAssessment],
        provider_provenance: dict[str, Any],
        now: str | None = None,
    ) -> SkillMaterialization:
        timestamp = now or now_iso()
        fingerprint = candidate.fingerprint()
        existing = self._find_source_version(source.source_id, conversion.conversion_id, fingerprint)
        if existing:
            record, version = existing
            lifecycle = record["lifecycle"][version]
            return self._materialization(
                record, version, lifecycle, candidate, score, book_title,
                source.source_id, conversion.conversion_id,
            )

        skill_id = str(uuid.uuid4())
        base = ascii_slug(candidate.title, fallback_prefix="apply-source-method")
        name = self._unique_name(f"{base[:52]}-{source.source_id[:8]}")
        version = "1.0.0"
        version_root = Path("05_Knowledge/Skills") / name / "versions" / version
        evidence_ids = sorted({item for statement in candidate.source_statements() for item in statement.evidence_ids})
        version_record = {
            "skill_id": skill_id,
            "version": version,
            "kind": "source-candidate",
            "created_at": timestamp,
            "content": candidate.semantic_payload(),
            "semantic_text": candidate.semantic_text(),
            "content_fingerprint": fingerprint,
            "evidence_bindings": evidence_ids,
            "sources": [{
                "source_id": source.source_id,
                "conversion_id": conversion.conversion_id,
                "book_title": book_title,
                "original_name": source.original_name,
                "original_path": "",
                "evidence_ids": evidence_ids,
            }],
            "provider": candidate.provider,
            "provider_provenance": provider_provenance,
            "semantic_assessments": [item.as_dict() for item in assessments],
            "quality_at_creation": score.as_dict(),
            "artifact_path": (version_root / "SKILL.md").as_posix(),
            "artifact_sha256": None,
            "evidence_file_sha256": None,
            "version_record_sha256": None,
        }
        lifecycle = self._empty_lifecycle(
            status="review" if score.status == "review" else "blocked" if score.status == "blocked" else "candidate"
        )
        record = {
            "skill_id": skill_id,
            "canonical_name": name,
            "title": candidate.title,
            "aliases": [],
            "created_at": timestamp,
            "current_version": version,
            "versions": {version: version_record},
            "lifecycle": {version: lifecycle},
            "relations": [],
        }
        self.records[skill_id] = record
        self.by_name[name] = skill_id
        self.data["updated_at"] = timestamp
        return self._materialization(
            record, version, lifecycle, candidate, score, book_title,
            source.source_id, conversion.conversion_id,
        )

    def create_synthesis_version(
        self,
        name: str,
        title: str,
        candidate: SkillCandidate,
        sources: list[dict[str, Any]],
        score: QualityScore,
        assessments: Iterable[SemanticAssessment],
        proposal_id: str,
        provider_provenance: dict[str, Any],
        now: str | None = None,
    ) -> SkillMaterialization:
        timestamp = now or now_iso()
        if name in self.by_name:
            record = self.require(name)
            skill_id = str(record["skill_id"])
            version = self._increment_minor(max(record["versions"], key=self._semver_key))
        else:
            skill_id = str(uuid.uuid4())
            name = self._unique_name(ascii_slug(name, fallback_prefix="apply-synthesized-method"))
            version = "1.0.0"
            record = {
                "skill_id": skill_id,
                "canonical_name": name,
                "title": title,
                "aliases": [],
                "created_at": timestamp,
                "current_version": version,
                "versions": {},
                "lifecycle": {},
                "relations": [],
            }
            self.records[skill_id] = record
            self.by_name[name] = skill_id
        evidence_ids = sorted({item for statement in candidate.source_statements() for item in statement.evidence_ids})
        source_pairs = {(item.get("source_id"), item.get("conversion_id")) for item in sources}
        if len(source_pairs) < 2:
            raise ValueError("A synthesis Skill must bind at least two source/conversion pairs.")
        version_root = Path("05_Knowledge/Skills") / name / "versions" / version
        record["versions"][version] = {
            "skill_id": skill_id,
            "version": version,
            "kind": "synthesis",
            "proposal_id": proposal_id,
            "created_at": timestamp,
            "content": candidate.semantic_payload(),
            "semantic_text": candidate.semantic_text(),
            "content_fingerprint": candidate.fingerprint(),
            "evidence_bindings": evidence_ids,
            "sources": sources,
            "provider": candidate.provider,
            "provider_provenance": provider_provenance,
            "semantic_assessments": [item.as_dict() for item in assessments],
            "quality_at_creation": score.as_dict(),
            "artifact_path": (version_root / "SKILL.md").as_posix(),
            "artifact_sha256": None,
            "evidence_file_sha256": None,
            "version_record_sha256": None,
        }
        record["lifecycle"][version] = self._empty_lifecycle(status="review")
        record["current_version"] = version
        self.data["updated_at"] = timestamp
        first = sources[0]
        return self._materialization(
            record, version, record["lifecycle"][version], candidate, score, title,
            str(first["source_id"]), str(first["conversion_id"]),
        )

    def seal_version(
        self,
        identifier: str,
        version: str,
        artifact_sha256: str,
        evidence_file_sha256: str,
        original_paths: dict[str, str] | None = None,
        version_record_sha256: str | None = None,
    ) -> None:
        record, version_record, _ = self.version(identifier, version)
        for field, incoming in (
            ("artifact_sha256", artifact_sha256),
            ("evidence_file_sha256", evidence_file_sha256),
            ("version_record_sha256", version_record_sha256),
        ):
            if incoming is None:
                continue
            if version_record.get(field) not in {None, incoming}:
                raise ValueError(f"Immutable Skill version {field} conflict.")
            version_record[field] = incoming
        if original_paths:
            for source in version_record.get("sources", []):
                source_id = str(source.get("source_id", ""))
                if source_id in original_paths:
                    source["original_path"] = original_paths[source_id]
        record["current_version"] = version

    def record_eval(
        self,
        identifier: str,
        version: str,
        eval_payload: dict[str, Any],
        evaluation_path: str,
        now: str | None = None,
    ) -> dict[str, Any]:
        record, version_record, lifecycle = self.version(identifier, version)
        if eval_payload.get("kind") != "real-trigger-eval" or not isinstance(eval_payload.get("cases"), list):
            raise ValueError("record-eval rejects boolean-only or synthetic payloads; a real evaluator artifact is required.")
        if eval_payload.get("skill") != record["canonical_name"] or eval_payload.get("version") != version:
            raise ValueError("Trigger eval Skill/version binding mismatch.")
        if eval_payload.get("artifact_sha256") != version_record.get("artifact_sha256"):
            raise ValueError("Trigger eval belongs to an old or different Skill artifact.")
        cases = eval_payload["cases"]
        positive = [item for item in cases if item.get("category") == "positive"]
        negative = [item for item in cases if item.get("category") == "negative"]
        boundary = [item for item in cases if item.get("category") == "boundary"]
        if len(positive) < 10 or len(negative) < 10 or len(boundary) < 5:
            raise ValueError("Real trigger eval requires at least 10 positive, 10 negative, and 5 boundary cases.")
        if any("observed" not in item or "passed" not in item or "transcript_sha256" not in item for item in cases):
            raise ValueError("Real trigger eval lacks observed results or transcript provenance.")
        evaluator = eval_payload.get("evaluator")
        if not isinstance(evaluator, dict) or evaluator.get("kind") not in REAL_EVALUATORS:
            raise ValueError("Trigger eval evaluator provenance is missing or not approved.")
        recall = 100.0 * sum(bool(item["observed"]) for item in positive) / len(positive)
        precision = 100.0 * sum(not bool(item["observed"]) for item in negative) / len(negative)
        boundary_score = 100.0 * sum(bool(item["passed"]) for item in boundary) / len(boundary)
        eval_id = "eval-" + str(uuid.uuid4())
        evaluation = {
            "eval_id": eval_id,
            "skill_id": record["skill_id"],
            "version": version,
            "artifact_sha256": version_record["artifact_sha256"],
            "case_set_sha256": eval_payload["case_set_sha256"],
            "run_id": eval_payload["run_id"],
            "evaluation_path": evaluation_path,
            "created_at": now or now_iso(),
            "evaluator": evaluator,
            "case_count": len(cases),
            "false_positives": list(eval_payload.get("false_positives", [])),
            "false_negatives": list(eval_payload.get("false_negatives", [])),
            "trigger_recall": recall,
            "trigger_precision": precision,
            "boundary_score": boundary_score,
            "passed": bool(eval_payload.get("passed")) and recall >= 80 and precision >= 80 and boundary_score >= 80,
        }
        lifecycle.setdefault("evals", []).append(evaluation)
        lifecycle["quality"] = SkillQualityEvaluator.with_eval(
            lifecycle.get("quality") or version_record["quality_at_creation"],
            precision, recall, boundary_score,
        )
        lifecycle["updated_at"] = now or now_iso()
        return evaluation

    def append_semantic_review(self, identifier: str, version: str, event: dict[str, Any]) -> None:
        _record, version_record, lifecycle = self.version(identifier, version)
        statement_hash = str(event.get("statement_hash", ""))
        originals = {str(item.get("statement_hash")): item for item in version_record.get("semantic_assessments", [])}
        if statement_hash not in originals:
            raise ValueError("Semantic review statement hash is not part of this immutable Skill version.")
        lifecycle.setdefault("semantic_reviews", []).append(event)
        effective = self.effective_assessments(identifier, version)
        weights = {"supported": 100.0, "partial": 50.0, "conflict": 20.0, "unsupported": 0.0}
        semantic = sum(weights.get(str(item.get("status")), 0.0) for item in effective) / max(len(effective), 1)
        quality = dict(lifecycle.get("quality") or version_record.get("quality_at_creation", {}))
        quality["semantic_support"] = round(semantic, 2)
        quality["conflict_coverage"] = 100.0 if not any(item.get("status") == "conflict" for item in effective) else 0.0
        quality["status"] = SkillQualityEvaluator._status(quality)
        quality["overall"] = SkillQualityEvaluator._overall(quality)
        lifecycle["quality"] = quality
        lifecycle["unresolved_reviews"] = [
            item["statement_hash"] for item in effective if item.get("status") != "supported"
        ]
        lifecycle["status"] = "review" if lifecycle["unresolved_reviews"] else "candidate"
        lifecycle["updated_at"] = now_iso()

    def effective_assessments(self, identifier: str, version: str) -> list[dict[str, Any]]:
        _, version_record, lifecycle = self.version(identifier, version)
        effective = {
            str(item.get("statement_hash")): dict(item)
            for item in version_record.get("semantic_assessments", [])
        }
        for review in lifecycle.get("semantic_reviews", []):
            statement_hash = str(review.get("statement_hash", ""))
            if statement_hash in effective:
                effective[statement_hash] = {
                    **effective[statement_hash],
                    "status": review.get("decision"),
                    "reason": review.get("reason"),
                    "evidence_ids": review.get("evidence_ids", []),
                    "reviewer": review.get("reviewer"),
                    "reviewed_at": review.get("reviewed_at"),
                }
        return list(effective.values())

    def approve(self, identifier: str, version: str, approver: str, reason: str, now: str | None = None) -> dict[str, Any]:
        record, version_record, lifecycle = self.version(identifier, version)
        if lifecycle.get("status") not in {"candidate", "review"}:
            raise ValueError("Only candidate/review lifecycle states can be approved.")
        assessments = self.effective_assessments(identifier, version)
        if not assessments or any(item.get("status") != "supported" for item in assessments):
            raise ValueError("Semantic support has unresolved, partial, conflict, or unsupported statements.")
        quality = lifecycle.get("quality") or version_record.get("quality_at_creation", {})
        required_quality = {
            "provenance_integrity": 100.0,
            "semantic_support": 100.0,
            "executability": 100.0,
            "conflict_coverage": 100.0,
            "conversion_confidence": 100.0,
        }
        failed = [name for name, threshold in required_quality.items() if float(quality.get(name, 0)) < threshold]
        if failed or quality.get("status") != "pass":
            raise ValueError("Approval quality gates are not all pass: " + ", ".join(failed or ["quality.status"]))
        evals = lifecycle.get("evals", [])
        latest = evals[-1] if evals else None
        if (
            not isinstance(latest, dict)
            or not bool(latest.get("passed"))
            or latest.get("version") != version
            or latest.get("artifact_sha256") != version_record.get("artifact_sha256")
            or not isinstance(latest.get("evaluator"), dict)
            or latest["evaluator"].get("kind") not in REAL_EVALUATORS
            or any(float(latest.get(key, 0)) < 80 for key in ("trigger_precision", "trigger_recall", "boundary_score"))
        ):
            raise ValueError("Latest real evaluator result for the exact artifact/version has not passed.")
        if float(quality.get("duplicate_overlap", 100)) >= SkillQualityEvaluator.HIGH_DUPLICATE_THRESHOLD:
            raise ValueError("Duplicate blocker remains unresolved.")
        if lifecycle.get("unresolved_reviews"):
            raise ValueError("Human review items remain unresolved.")
        timestamp = now or now_iso()
        approval = {
            "approval_id": "approval-" + str(uuid.uuid4()),
            "approver": approver,
            "reason": reason,
            "approved_at": timestamp,
            "artifact_sha256": version_record["artifact_sha256"],
            "eval_id": latest["eval_id"],
        }
        lifecycle.setdefault("approvals", []).append(approval)
        lifecycle["status"] = "approved"
        lifecycle["updated_at"] = timestamp
        record["current_version"] = version
        self.data["updated_at"] = timestamp
        return approval

    def reject(self, identifier: str, version: str, reviewer: str, reason: str) -> None:
        _, _, lifecycle = self.version(identifier, version)
        lifecycle["status"] = "rejected"
        lifecycle.setdefault("decisions", []).append({
            "action": "reject", "reviewer": reviewer, "reason": reason, "at": now_iso(),
        })

    def deprecate(self, identifier: str, version: str, reviewer: str, reason: str) -> None:
        _, _, lifecycle = self.version(identifier, version)
        lifecycle["status"] = "deprecated"
        lifecycle.setdefault("decisions", []).append({
            "action": "deprecate", "reviewer": reviewer, "reason": reason, "at": now_iso(),
        })

    def rollback(self, identifier: str, version: str, reviewer: str, reason: str) -> dict[str, Any]:
        record, _, lifecycle = self.version(identifier, version)
        if lifecycle.get("status") not in {"approved", "deprecated"}:
            raise ValueError("Rollback target must be an approved or deprecated reproducible version.")
        previous = record.get("current_version")
        record["current_version"] = version
        event = {
            "action": "rollback", "from": previous, "to": version,
            "reviewer": reviewer, "reason": reason, "at": now_iso(),
        }
        record.setdefault("relations", []).append(event)
        return event

    def record_use(
        self,
        identifier: str,
        version: str,
        project: str,
        outcome: str,
        feedback: str | None,
        event_id: str,
        now: str | None = None,
    ) -> dict[str, Any]:
        if outcome not in {"success", "partial", "failed"}:
            raise ValueError("outcome must be success, partial, or failed.")
        if event_id in self.data["usage_events"]:
            return self.data["usage_events"][event_id]
        record, version_record, lifecycle = self.version(identifier, version)
        timestamp = now or now_iso()
        event = {
            "event_id": event_id,
            "skill_id": record["skill_id"],
            "name": record["canonical_name"],
            "version": version,
            "project": project,
            "outcome": outcome,
            "feedback": feedback or "",
            "created_at": timestamp,
        }
        self.data["usage_events"][event_id] = event
        usage = lifecycle.setdefault("usage", {
            "invocation_count": 0,
            "outcomes": {"success": 0, "partial": 0, "failed": 0},
            "feedback": [],
            "projects": [],
        })
        usage["invocation_count"] += 1
        usage["outcomes"][outcome] += 1
        usage["feedback"].append({
            "event_id": event_id, "text": feedback or "", "outcome": outcome, "created_at": timestamp,
        })
        if project not in usage["projects"]:
            usage["projects"].append(project)
            usage["projects"].sort()
        lifecycle["quality"] = SkillQualityEvaluator.with_usage(
            lifecycle.get("quality") or version_record["quality_at_creation"], usage["outcomes"],
        )
        lifecycle["updated_at"] = timestamp
        self.data["updated_at"] = timestamp
        return event

    def compare(self, identifier: str, left: str, right: str) -> str:
        _, left_record, _ = self.version(identifier, left)
        _, right_record, _ = self.version(identifier, right)
        left_lines = json.dumps(left_record["content"], ensure_ascii=False, indent=2, sort_keys=True).splitlines()
        right_lines = json.dumps(right_record["content"], ensure_ascii=False, indent=2, sort_keys=True).splitlines()
        diff = "\n".join(difflib.unified_diff(left_lines, right_lines, fromfile=left, tofile=right, lineterm=""))
        if not diff:
            return (
                f"--- {left}\n+++ {right}\n"
                "Versions have content-identical candidate payloads; lifecycle, provenance, and usage remain version-specific.\n"
            )
        return diff + "\n"

    def to_json(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def to_markdown(self) -> str:
        lines = [
            "---", 'title: "Skill Registry"', 'type: "system"', 'status: "active"',
            'engine_version: "3.3.0"', "---", "", "# Skill Registry", "",
            "> Version content and lifecycle are separated; usage and real eval records are version-bound.", "",
            "| Skill | Current | Status | Versions | Calls(current) |",
            "|---|---:|---|---:|---:|",
        ]
        for record in sorted(self.records.values(), key=lambda item: item["canonical_name"]):
            current = str(record["current_version"])
            lifecycle = record["lifecycle"][current]
            calls = lifecycle.get("usage", {}).get("invocation_count", 0)
            path = Path("05_Knowledge/Skills") / record["canonical_name"] / "SKILL.md"
            lines.append(
                f"| {wikilink(path, record['title'])} | {current} | {lifecycle['status']} | {len(record['versions'])} | {calls} |"
            )
        return "\n".join(lines) + "\n"

    @classmethod
    def schema(cls) -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["schema_version", "engine_version", "revision", "skills", "name_index", "usage_events"],
            "properties": {
                "schema_version": {"const": 3},
                "engine_version": {"type": "string"},
                "revision": {"type": "integer", "minimum": 0},
                "updated_at": {"type": ["string", "null"]},
                "skills": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "required": ["skill_id", "canonical_name", "current_version", "versions", "lifecycle"],
                    },
                },
                "name_index": {"type": "object", "additionalProperties": {"type": "string"}},
                "usage_events": {"type": "object", "additionalProperties": {"type": "object"}},
            },
        }

    def _find_source_version(self, source_id: str, conversion_id: str, fingerprint: str) -> tuple[dict[str, Any], str] | None:
        for record in self.records.values():
            for version, version_record in record.get("versions", {}).items():
                pairs = {
                    (item.get("source_id"), item.get("conversion_id"))
                    for item in version_record.get("sources", [])
                }
                if (source_id, conversion_id) in pairs and version_record.get("content_fingerprint") == fingerprint:
                    return record, version
        return None

    def _unique_name(self, base: str) -> str:
        base = base[:63].rstrip("-")
        name = base
        suffix = 2
        while name in self.by_name:
            name = f"{base[:59]}-{suffix}"
            suffix += 1
        return name

    @staticmethod
    def _empty_lifecycle(status: str = "candidate") -> dict[str, Any]:
        return {
            "status": status,
            "approvals": [],
            "decisions": [],
            "evals": [],
            "quality": None,
            "usage": {
                "invocation_count": 0,
                "outcomes": {"success": 0, "partial": 0, "failed": 0},
                "feedback": [],
                "projects": [],
            },
            "exposures": [],
            "semantic_reviews": [],
            "unresolved_reviews": [],
            "updated_at": now_iso(),
        }

    @staticmethod
    def _semver_key(version: str) -> tuple[int, int, int]:
        try:
            return tuple(int(item) for item in version.split("."))  # type: ignore[return-value]
        except (TypeError, ValueError):
            return (0, 0, 0)

    @classmethod
    def _increment_minor(cls, version: str) -> str:
        major, minor, _patch = cls._semver_key(version)
        return f"{major}.{minor + 1}.0"

    @staticmethod
    def _materialization(
        record: dict[str, Any],
        version: str,
        lifecycle: dict[str, Any],
        candidate: SkillCandidate,
        score: QualityScore,
        title: str,
        source_id: str,
        conversion_id: str,
    ) -> SkillMaterialization:
        return SkillMaterialization(
            skill_id=str(record["skill_id"]),
            name=str(record["canonical_name"]),
            version=version,
            status=str(lifecycle["status"]),
            candidate=candidate,
            score=score,
            source_title=title,
            source_id=source_id,
            conversion_id=conversion_id,
            version_path=str(record["versions"][version]["artifact_path"]),
        )
