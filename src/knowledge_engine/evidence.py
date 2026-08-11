from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .models import Evidence, GroundedStatement, ProviderResult, ValidationReport
from .utils import canonical_json, normalize_excerpt, normalize_text, sha256_text


def evidence_id_for(conversion_id: str, locator: str, excerpt: str) -> str:
    payload = f"{conversion_id}\0{locator}\0{normalize_excerpt(excerpt)}"
    return "ev-" + sha256_text(payload)


@dataclass
class EvidenceCatalog:
    source_title: str
    source_id: str
    conversion_id: str
    source_text: str
    original_path: str
    items: dict[str, Evidence]

    @property
    def source_sha256(self) -> str:
        return self.source_id

    @classmethod
    def from_text(
        cls,
        title: str,
        source_id: str,
        text: str,
        conversion_id: str | None = None,
        original_path: str = "",
        conversion_locator_for: Callable[[int, int], str] | None = None,
    ) -> "EvidenceCatalog":
        normalized_text = normalize_text(text)
        conversion = conversion_id or sha256_text(f"legacy\0{source_id}\0{normalized_text}")
        lines = normalized_text.splitlines()
        items: dict[str, Evidence] = {}
        section = title
        index = 0
        while index < len(lines):
            stripped = lines[index].strip()
            heading = re.match(r"^#{1,6}\s+(.+?)\s*$", stripped)
            if heading:
                section = heading.group(1).strip()
                index += 1
                continue
            if not stripped:
                index += 1
                continue
            start = index + 1
            block = [stripped]
            if not re.match(r"^(?:[-*+]\s+|\d+[.)、]\s*)", stripped):
                cursor = index + 1
                while cursor < len(lines) and lines[cursor].strip() and not re.match(r"^#{1,6}\s+", lines[cursor].strip()):
                    candidate = lines[cursor].strip()
                    if re.match(r"^(?:[-*+]\s+|\d+[.)、]\s*)", candidate) or sum(len(item) for item in block) >= 450:
                        break
                    block.append(candidate)
                    cursor += 1
                index = cursor
            else:
                index += 1
            end = start + len(block) - 1
            excerpt = " ".join(block)
            locator = f"lines:{start}-{end}"
            evidence_id = evidence_id_for(conversion, locator, excerpt)
            evidence = Evidence(
                evidence_id=evidence_id,
                section=section,
                start_line=start,
                end_line=end,
                excerpt=excerpt,
                source_id=source_id,
                conversion_id=conversion,
                excerpt_sha256=sha256_text(normalize_excerpt(excerpt)),
                original_path=original_path,
                source_sha256=source_id,
                conversion_locator=(conversion_locator_for(start, end) if conversion_locator_for else locator),
            )
            collision = items.get(evidence_id)
            if collision is not None and collision.excerpt != evidence.excerpt:
                raise ValueError(f"Evidence ID collision: {evidence_id}")
            items[evidence_id] = evidence
        return cls(title, source_id, conversion, normalized_text, original_path, items)

    @classmethod
    def from_dict(cls, value: dict[str, object], source_text: str) -> "EvidenceCatalog":
        raw_items = value.get("items", [])
        if not isinstance(raw_items, list):
            raise ValueError("Evidence catalog items 必须是数组。")
        items: dict[str, Evidence] = {}
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise ValueError("Evidence item 必须是对象。")
            evidence = Evidence(
                evidence_id=str(raw.get("evidence_id", "")),
                source_id=str(raw.get("source_id", "")),
                conversion_id=str(raw.get("conversion_id", "")),
                section=str(raw.get("section", "")),
                start_line=int(raw.get("start_line", 0)),
                end_line=int(raw.get("end_line", 0)),
                excerpt=str(raw.get("excerpt", "")),
                excerpt_sha256=str(raw.get("excerpt_sha256", "")),
                original_path=str(raw.get("original_path", "")),
                source_sha256=str(raw.get("source_id", "")),
                conversion_locator=str(raw.get("conversion_locator", raw.get("locator", ""))),
            )
            prior = items.get(evidence.evidence_id)
            if prior and prior.excerpt != evidence.excerpt:
                raise ValueError(f"Evidence ID 被重绑定：{evidence.evidence_id}")
            items[evidence.evidence_id] = evidence
        return cls(
            source_title=str(value.get("source_title", "")),
            source_id=str(value.get("source_id", "")),
            conversion_id=str(value.get("conversion_id", "")),
            source_text=normalize_text(source_text),
            original_path=str(value.get("original_path", "")),
            items=items,
        )

    @classmethod
    def load(cls, catalog_path: Path, source_text_path: Path) -> "EvidenceCatalog":
        return cls.from_dict(json.loads(catalog_path.read_text(encoding="utf-8")), source_text_path.read_text(encoding="utf-8"))

    def get(self, evidence_id: str) -> Evidence | None:
        return self.items.get(evidence_id)

    def ids_for_locator(self, start_line: int, end_line: int) -> tuple[str, ...]:
        return tuple(
            item.evidence_id for item in self.items.values()
            if item.start_line <= end_line and item.end_line >= start_line
        )

    def ids_for_evidence(self, evidence: Evidence) -> tuple[str, ...]:
        matches = self.ids_for_locator(evidence.start_line, evidence.end_line)
        if matches:
            return matches
        normalized = normalize_excerpt(evidence.excerpt)
        return tuple(item.evidence_id for item in self.items.values() if normalized and normalized in normalize_excerpt(item.excerpt))

    def as_prompt_items(self) -> list[dict[str, object]]:
        return [self.items[key].as_dict() for key in sorted(self.items)]

    def subset_for_range(self, start_line: int, end_line: int, owned_start_line: int | None = None) -> "EvidenceCatalog":
        """Return only system-generated Evidence owned by a chunk.

        Evidence crossing the overlap boundary belongs to the earlier chunk. This
        keeps map results order-independent and prevents double attribution.
        """
        owner_start = start_line if owned_start_line is None else max(start_line, owned_start_line)
        selected = {
            evidence_id: item
            for evidence_id, item in self.items.items()
            if item.start_line >= owner_start and item.end_line <= end_line
        }
        if not selected:
            selected = {
                evidence_id: item
                for evidence_id, item in self.items.items()
                if item.start_line <= end_line and item.end_line >= owner_start
            }
        return EvidenceCatalog(
            source_title=self.source_title,
            source_id=self.source_id,
            conversion_id=self.conversion_id,
            source_text=self.source_text,
            original_path=self.original_path,
            items=selected,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 3,
            "source_title": self.source_title,
            "source_id": self.source_id,
            "conversion_id": self.conversion_id,
            "source_text_sha256": sha256_text(self.source_text),
            "original_path": self.original_path,
            "items": [self.items[key].as_dict() for key in sorted(self.items)],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def verify_integrity(self) -> ValidationReport:
        report = ValidationReport()
        lines = self.source_text.splitlines()
        seen: dict[str, str] = {}
        for evidence in self.items.values():
            actual = " ".join(line.strip() for line in lines[evidence.start_line - 1:evidence.end_line] if line.strip())
            normalized = normalize_excerpt(actual)
            if normalized != normalize_excerpt(evidence.excerpt):
                report.add("error", "evidence-text-mismatch", f"Evidence {evidence.evidence_id} 与转换快照定位内容不一致。")
            excerpt_hash = sha256_text(normalize_excerpt(evidence.excerpt))
            if evidence.excerpt_sha256 != excerpt_hash:
                report.add("error", "evidence-excerpt-hash", f"Evidence {evidence.evidence_id} 摘录哈希不一致。")
            if evidence.source_id != self.source_id or evidence.conversion_id != self.conversion_id:
                report.add("error", "evidence-identity-mismatch", f"Evidence {evidence.evidence_id} 身份绑定错误。")
            expected_id = evidence_id_for(self.conversion_id, evidence.locator, evidence.excerpt)
            if expected_id != evidence.evidence_id:
                report.add("error", "evidence-id-mismatch", f"Evidence {evidence.evidence_id} 无法由 conversion/locator/excerpt 重算。")
            prior = seen.setdefault(evidence.evidence_id, canonical_json(evidence.as_dict()))
            if prior != canonical_json(evidence.as_dict()):
                report.add("error", "evidence-rebinding", f"Evidence ID 被重绑定：{evidence.evidence_id}")
            if not re.fullmatch(r"lines:\d+-\d+", evidence.conversion_locator or evidence.locator):
                report.add("error", "evidence-conversion-locator", f"Evidence {evidence.evidence_id} 缺少可验证 conversion locator。")
        return report


@dataclass
class EvidenceUniverse:
    items: dict[str, Evidence]

    @classmethod
    def from_catalogs(cls, catalogs: Iterable[EvidenceCatalog]) -> "EvidenceUniverse":
        items: dict[str, Evidence] = {}
        for catalog in catalogs:
            for evidence_id, evidence in catalog.items.items():
                prior = items.get(evidence_id)
                if prior and canonical_json(prior.as_dict()) != canonical_json(evidence.as_dict()):
                    raise ValueError(f"Evidence ID 被跨目录重绑定：{evidence_id}")
                items[evidence_id] = evidence
        return cls(items)

    def get(self, evidence_id: str) -> Evidence | None:
        return self.items.get(evidence_id)


class EvidenceGate:
    def validate(self, result: ProviderResult, catalog: EvidenceCatalog) -> ValidationReport:
        report = catalog.verify_integrity()
        for candidate in result.concepts:
            self._validate_candidate_identity(candidate.title, "concept", report)
            self._validate_statements(candidate.all_statements(), catalog, report, f"concept:{candidate.title}")
        for candidate in result.relations:
            self._validate_candidate_identity(candidate.source_concept, "relation-source", report)
            self._validate_candidate_identity(candidate.target_concept, "relation-target", report)
            if candidate.source_concept.casefold() == candidate.target_concept.casefold():
                report.add("error", "self-relation", "概念关系不能连接同一概念。", candidate.source_concept)
            self._validate_statements(candidate.all_statements(), catalog, report, f"relation:{candidate.source_concept}->{candidate.target_concept}")
        for candidate in result.skills:
            self._validate_candidate_identity(candidate.title, "skill", report)
            self._validate_statements(candidate.all_statements(), catalog, report, f"skill:{candidate.title}")
            if len(candidate.actions) < 2:
                report.add("error", "skill-actions", "Skill候选必须至少包含两个执行动作。", candidate.title)
            if len(candidate.decision_flow) < 2:
                report.add("error", "skill-decisions", "Skill候选必须至少包含两个判断节点。", candidate.title)
        return report

    @staticmethod
    def _validate_candidate_identity(title: str, kind: str, report: ValidationReport) -> None:
        if len(title.strip()) < 2 or len(title.strip()) > 100:
            report.add("error", "candidate-title", f"{kind} 候选标题为空、过短或过长。", title)

    @staticmethod
    def _validate_statements(statements: Iterable[GroundedStatement], catalog: EvidenceCatalog, report: ValidationReport, path: str) -> None:
        for index, statement in enumerate(statements, start=1):
            if not statement.text.strip():
                report.add("error", "empty-grounded-statement", "候选包含空陈述。", f"{path}:{index}")
            if statement.origin not in {"source", "engine_policy"}:
                report.add("error", "statement-origin", "陈述 origin 必须是 source 或 engine_policy。", f"{path}:{index}")
            if statement.origin == "source" and not statement.evidence_ids:
                report.add("error", "ungrounded-statement", "来源陈述没有绑定 Evidence。", f"{path}:{index}")
            if statement.origin == "engine_policy" and statement.evidence_ids:
                report.add("error", "policy-evidence-mix", "engine_policy 不得伪装为来源 Evidence。", f"{path}:{index}")
            for evidence_id in statement.evidence_ids:
                evidence = catalog.get(evidence_id)
                if evidence is None:
                    report.add("error", "unknown-evidence", f"引用了不存在的 Evidence：{evidence_id}", f"{path}:{index}")
                elif evidence.source_id != catalog.source_id or evidence.conversion_id != catalog.conversion_id:
                    report.add("error", "cross-source-evidence", f"Evidence {evidence_id} 不属于当前转换快照。", f"{path}:{index}")
