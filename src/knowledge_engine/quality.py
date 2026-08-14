from __future__ import annotations

import re
from typing import Any, Iterable

from .evidence import EvidenceCatalog
from .models import QualityScore, SemanticAssessment, SkillCandidate
from .gates import SkillExecutabilityGate


class SkillQualityEvaluator:
    HIGH_DUPLICATE_THRESHOLD = 85.0
    _AUDITED_NON_TEXT_WARNING_PATTERNS = (
        re.compile(r"^EPUB contains \d+ declared image omission\(s\)\.$"),
        re.compile(
            r"^\d+ image reference\(s\) were declared as omitted; "
            r"no visual claims were inferred\.$"
        ),
    )

    @classmethod
    def material_conversion_warnings(cls, warnings: Iterable[str]) -> list[str]:
        """Return warnings that can reduce confidence in text-grounded claims.

        Canonical conversion keeps image omissions in the audit trail. When the
        converter also attests that no visual claim was inferred, that omission
        does not make the decoded text or its Evidence less trustworthy.
        """
        material: list[str] = []
        for item in warnings:
            warning = str(item).strip()
            if not warning:
                continue
            if any(pattern.fullmatch(warning) for pattern in cls._AUDITED_NON_TEXT_WARNING_PATTERNS):
                continue
            material.append(warning)
        return material

    def score(
        self,
        candidate: SkillCandidate,
        catalog: EvidenceCatalog,
        registry_records: dict[str, dict[str, Any]],
        assessments: Iterable[SemanticAssessment] = (),
        conversion_warnings: Iterable[str] = (),
    ) -> QualityScore:
        statements = candidate.all_statements()
        source_statements = candidate.source_statements()
        grounded = sum(
            1 for statement in source_statements
            if statement.evidence_ids and all(catalog.get(evidence_id) is not None for evidence_id in statement.evidence_ids)
        )
        provenance = 100.0 * grounded / max(len(source_statements), 1)

        assessed = list(assessments)
        weights = {"supported": 100.0, "partial": 50.0, "conflict": 20.0, "unsupported": 0.0}
        semantic = sum(weights[item.status] for item in assessed) / max(len(assessed), 1)

        executability = SkillExecutabilityGate().score(candidate)
        duplicate = self._duplicate_overlap(candidate, registry_records)
        conflict_coverage = 100.0 if not any(item.status == "conflict" for item in assessed) else 0.0
        warnings = self.material_conversion_warnings(conversion_warnings)
        conversion_confidence = max(0.0, 100.0 - 25.0 * len(warnings))

        trigger_precision = None
        trigger_recall = None
        version_eval = None
        usage = None
        overall = (
            provenance * 0.20 + semantic * 0.20 + executability * 0.15
            + (100.0 - duplicate) * 0.10 + conflict_coverage * 0.10
            + conversion_confidence * 0.10 + 0.15 * 50.0
        )
        notes: list[str] = []
        status = "pass"
        if warnings:
            status = "review"
            notes.append("转换存在 warning；禁止自动批准。")
        if duplicate >= self.HIGH_DUPLICATE_THRESHOLD:
            status = "blocked"
            notes.append("与现有 Skill 高度重复；必须走综合/同名治理，不得生成可调用版本。")
        if semantic < 100:
            status = "blocked" if any(item.status == "unsupported" for item in assessed) else "review"
            notes.append("语义支持不是全量 supported；需要人工审查。")
        if provenance < 100 or executability < 100 or conflict_coverage < 100:
            status = "blocked"
        notes.append("版本尚无 eval 与真实 usage；这两项不会从旧版本继承。")
        return QualityScore(
            provenance, semantic, executability, trigger_precision, trigger_recall,
            duplicate, conflict_coverage, version_eval, usage,
            conversion_confidence, overall, status, tuple(notes),
        )

    @classmethod
    def with_eval(cls, quality: dict[str, Any], precision: float, recall: float, boundary: float) -> dict[str, Any]:
        updated = dict(quality)
        updated["trigger_precision"] = round(precision, 2)
        updated["trigger_recall"] = round(recall, 2)
        updated["boundary_score"] = round(boundary, 2)
        updated["version_specific_eval"] = round((precision + recall + boundary) / 3.0, 2)
        updated["status"] = cls._status(updated)
        updated["overall"] = cls._overall(updated)
        return updated

    @classmethod
    def with_usage(cls, quality: dict[str, Any], outcomes: dict[str, int]) -> dict[str, Any]:
        updated = dict(quality)
        total = sum(int(outcomes.get(key, 0)) for key in ("success", "partial", "failed"))
        effectiveness = None if total == 0 else (
            int(outcomes.get("success", 0)) * 100 + int(outcomes.get("partial", 0)) * 60
        ) / total
        updated["usage_effectiveness"] = None if effectiveness is None else round(effectiveness, 2)
        updated["overall"] = cls._overall(updated)
        return updated

    @classmethod
    def _status(cls, quality: dict[str, Any]) -> str:
        if float(quality.get("duplicate_overlap", 100)) >= cls.HIGH_DUPLICATE_THRESHOLD:
            return "blocked"
        if float(quality.get("provenance_integrity", 0)) < 100 or float(quality.get("semantic_support", 0)) < 100:
            return "blocked"
        if float(quality.get("executability", 0)) < 100 or float(quality.get("conflict_coverage", 0)) < 100:
            return "blocked"
        if float(quality.get("conversion_confidence", 0)) < 100:
            return "review"
        if (
            float(quality.get("trigger_precision") or 0) < 80
            or float(quality.get("trigger_recall") or 0) < 80
            or float(quality.get("boundary_score") or 0) < 80
        ):
            return "review"
        return "pass"

    @staticmethod
    def _overall(quality: dict[str, Any]) -> float:
        values = [
            float(quality.get("provenance_integrity", 0)),
            float(quality.get("semantic_support", 0)),
            float(quality.get("executability", 0)),
            100.0 - float(quality.get("duplicate_overlap", 100)),
            float(quality.get("conflict_coverage", 0)),
            float(quality.get("conversion_confidence", 0)),
        ]
        optional = [quality.get("version_specific_eval"), quality.get("usage_effectiveness")]
        values.extend(float(item) for item in optional if item is not None)
        return round(sum(values) / len(values), 2) if values else 0.0

    def _duplicate_overlap(self, candidate: SkillCandidate, records: dict[str, dict[str, Any]]) -> float:
        candidate_tokens = self._tokens(candidate.semantic_text())
        maximum = 0.0
        for record in records.values():
            versions = record.get("versions", {})
            if not isinstance(versions, dict):
                continue
            for version in versions.values():
                if not isinstance(version, dict):
                    continue
                existing = self._tokens(str(version.get("semantic_text", "")))
                if not candidate_tokens or not existing:
                    continue
                maximum = max(maximum, 100.0 * len(candidate_tokens & existing) / len(candidate_tokens | existing))
        return maximum

    @staticmethod
    def _tokens(value: str) -> set[str]:
        lowered = value.casefold()
        words = set(re.findall(r"[a-z0-9]{2,}", lowered))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
        words.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
        return {token for token in words if token}
