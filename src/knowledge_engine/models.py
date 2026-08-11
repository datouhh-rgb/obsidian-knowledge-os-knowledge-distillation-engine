from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal


SupportStatus = Literal["supported", "partial", "unsupported", "conflict"]


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    section: str
    start_line: int
    end_line: int
    excerpt: str
    source_id: str = ""
    conversion_id: str = ""
    excerpt_sha256: str = ""
    original_path: str = ""
    source_sha256: str = ""  # V2 compatibility alias; always equals source_id in V3.
    conversion_locator: str = ""

    @property
    def locator(self) -> str:
        return f"lines:{self.start_line}-{self.end_line}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id or self.source_sha256,
            "conversion_id": self.conversion_id,
            "section": self.section,
            "locator": self.locator,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "excerpt": self.excerpt,
            "excerpt_sha256": self.excerpt_sha256,
            "original_path": self.original_path,
            "normalized_locator": self.locator,
            "conversion_locator": self.conversion_locator or self.locator,
        }


@dataclass(frozen=True)
class Section:
    title: str
    level: int
    start_line: int
    end_line: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class Concept:
    title: str
    statement: str
    evidence: Evidence


@dataclass(frozen=True)
class Method:
    title: str
    steps: tuple[str, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Case:
    title: str
    statement: str
    evidence: Evidence


@dataclass
class Analysis:
    title: str
    sections: list[Section] = field(default_factory=list)
    concepts: list[Concept] = field(default_factory=list)
    methods: list[Method] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)
    chapter_evidence: list[Evidence] = field(default_factory=list)


@dataclass
class IngestedSource:
    input_ref: str
    local_path: Path
    original_name: str
    media_type: str
    source_kind: str
    sha256: str
    text: str
    converter_metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    identity_source_id: str = ""
    edition_id: str | None = None

    @property
    def source_id(self) -> str:
        return self.identity_source_id or self.sha256

    @property
    def original_hash(self) -> str:
        return self.sha256


@dataclass(frozen=True)
class SourceIdentity:
    """Immutable V3.2 identity joining an original artifact to one conversion."""

    source_id: str
    original_hash: str
    format: str
    converter: str
    converter_version: str
    conversion_id: str
    conversion_hash: str
    edition_id: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source_id": self.source_id,
            "original_hash": self.original_hash,
            "format": self.format,
            "converter": self.converter,
            "converter_version": self.converter_version,
            "conversion_id": self.conversion_id,
            "conversion_hash": self.conversion_hash,
            "edition_id": self.edition_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ConversionIdentity:
    source_id: str
    conversion_id: str
    normalized_text_sha256: str
    converter: str
    converter_version: str
    converter_config: dict[str, Any]
    warnings: tuple[str, ...]

    @property
    def conversion_hash(self) -> str:
        return self.normalized_text_sha256

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "conversion_id": self.conversion_id,
            "normalized_text_sha256": self.normalized_text_sha256,
            "converter": self.converter,
            "converter_version": self.converter_version,
            "converter_config": self.converter_config,
            "warnings": list(self.warnings),
            "conversion_hash": self.conversion_hash,
        }


@dataclass(frozen=True)
class GroundedStatement:
    text: str
    evidence_ids: tuple[str, ...]
    origin: Literal["source", "engine_policy"] = "source"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GroundedStatement":
        return cls(
            text=str(value.get("text", "")).strip(),
            evidence_ids=tuple(str(item).strip() for item in value.get("evidence_ids", []) if str(item).strip()),
            origin=str(value.get("origin", "source")),  # type: ignore[arg-type]
        )

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "evidence_ids": list(self.evidence_ids), "origin": self.origin}


@dataclass(frozen=True)
class ConceptCandidate:
    title: str
    definition: GroundedStatement
    provider: str
    confidence: float = 0.5

    def all_statements(self) -> tuple[GroundedStatement, ...]:
        return (self.definition,)


@dataclass(frozen=True)
class CanonicalConcept:
    canonical_concept_id: str
    canonical_title: str
    aliases: tuple[str, ...]
    source_mentions: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    merge_reason: str
    confidence: float
    definition: GroundedStatement
    provider: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_concept_id": self.canonical_concept_id,
            "canonical_title": self.canonical_title,
            "aliases": list(self.aliases),
            "source_mentions": list(self.source_mentions),
            "evidence_ids": list(self.evidence_ids),
            "merge_reason": self.merge_reason,
            "confidence": self.confidence,
            "definition": self.definition.as_dict(),
            "provider": self.provider,
        }

    def as_candidate(self) -> ConceptCandidate:
        return ConceptCandidate(self.canonical_title, self.definition, self.provider, self.confidence)


@dataclass(frozen=True)
class MethodCluster:
    """Evidence-bound grouping of stable concepts for procedural synthesis."""

    id: str
    title: str
    description: str
    concept_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    source_books: tuple[str, ...]
    confidence: float
    creation_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "concept_ids": list(self.concept_ids),
            "evidence_ids": list(self.evidence_ids),
            "source_books": list(self.source_books),
            "confidence": self.confidence,
            "creation_reason": self.creation_reason,
        }


@dataclass(frozen=True)
class ConceptConflictCandidate:
    conflict_id: str
    normalized_title: str
    candidates: tuple[ConceptCandidate, ...]
    reason: str
    status: Literal["unresolved"] = "unresolved"

    def as_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "normalized_title": self.normalized_title,
            "reason": self.reason,
            "status": self.status,
            "candidates": [
                {
                    "title": item.title,
                    "definition": item.definition.as_dict(),
                    "provider": item.provider,
                    "confidence": item.confidence,
                }
                for item in self.candidates
            ],
        }


@dataclass(frozen=True)
class ConceptRelationCandidate:
    source_concept: str
    target_concept: str
    relation: GroundedStatement
    provider: str
    confidence: float = 0.5

    def all_statements(self) -> tuple[GroundedStatement, ...]:
        return (self.relation,)


@dataclass(frozen=True)
class SkillCandidate:
    title: str
    positioning: GroundedStatement
    scenarios: tuple[GroundedStatement, ...]
    input_requirements: tuple[GroundedStatement, ...]
    decision_flow: tuple[GroundedStatement, ...]
    actions: tuple[GroundedStatement, ...]
    output_format: GroundedStatement
    boundaries: tuple[GroundedStatement, ...]
    prohibited_scenarios: tuple[GroundedStatement, ...]
    provider: str
    confidence: float = 0.5

    def all_statements(self) -> tuple[GroundedStatement, ...]:
        return (
            self.positioning,
            *self.scenarios,
            *self.input_requirements,
            *self.decision_flow,
            *self.actions,
            self.output_format,
            *self.boundaries,
            *self.prohibited_scenarios,
        )

    def source_statements(self) -> tuple[GroundedStatement, ...]:
        return tuple(item for item in self.all_statements() if item.origin == "source")

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "positioning": self.positioning.as_dict(),
            "scenarios": [item.as_dict() for item in self.scenarios],
            "input_requirements": [item.as_dict() for item in self.input_requirements],
            "decision_flow": [item.as_dict() for item in self.decision_flow],
            "actions": [item.as_dict() for item in self.actions],
            "output_format": self.output_format.as_dict(),
            "boundaries": [item.as_dict() for item in self.boundaries],
            "prohibited_scenarios": [item.as_dict() for item in self.prohibited_scenarios],
            "provider": self.provider,
            "confidence": self.confidence,
        }

    def fingerprint(self) -> str:
        payload = json.dumps(self.semantic_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def semantic_text(self) -> str:
        return " ".join([self.title, *(statement.text for statement in self.all_statements())])

    @classmethod
    def from_payload(cls, value: dict[str, Any]) -> "SkillCandidate":
        def items(key: str) -> tuple[GroundedStatement, ...]:
            return tuple(GroundedStatement.from_dict(item) for item in value.get(key, []))

        return cls(
            title=str(value["title"]),
            positioning=GroundedStatement.from_dict(value["positioning"]),
            scenarios=items("scenarios"),
            input_requirements=items("input_requirements"),
            decision_flow=items("decision_flow"),
            actions=items("actions"),
            output_format=GroundedStatement.from_dict(value["output_format"]),
            boundaries=items("boundaries"),
            prohibited_scenarios=items("prohibited_scenarios"),
            provider=str(value.get("provider", "unknown")),
            confidence=float(value.get("confidence", 0.5)),
        )


@dataclass
class ProviderResult:
    provider: str
    concepts: list[ConceptCandidate] = field(default_factory=list)
    relations: list[ConceptRelationCandidate] = field(default_factory=list)
    skills: list[SkillCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    comparisons: list[dict[str, Any]] = field(default_factory=list)

    def all_candidates(self) -> Iterable[ConceptCandidate | ConceptRelationCandidate | SkillCandidate]:
        yield from self.concepts
        yield from self.relations
        yield from self.skills


@dataclass(frozen=True)
class SemanticAssessment:
    statement_hash: str
    status: SupportStatus
    reason: str
    evidence_ids: tuple[str, ...]
    reviewer: str
    reviewed_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "statement_hash": self.statement_hash,
            "status": self.status,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
        }


@dataclass(frozen=True)
class QualityScore:
    provenance_integrity: float
    semantic_support: float
    executability: float
    trigger_precision: float | None
    trigger_recall: float | None
    duplicate_overlap: float
    conflict_coverage: float
    version_eval: float | None
    usage_effectiveness: float | None
    conversion_confidence: float
    overall: float
    status: str
    notes: tuple[str, ...] = ()

    @property
    def evidence_completeness(self) -> float:
        return self.provenance_integrity

    @property
    def duplicate_risk(self) -> float:
        return self.duplicate_overlap

    def as_dict(self) -> dict[str, Any]:
        return {
            "provenance_integrity": round(self.provenance_integrity, 2),
            "semantic_support": round(self.semantic_support, 2),
            "executability": round(self.executability, 2),
            "trigger_precision": self.trigger_precision,
            "trigger_recall": self.trigger_recall,
            "duplicate_overlap": round(self.duplicate_overlap, 2),
            "conflict_coverage": round(self.conflict_coverage, 2),
            "version_specific_eval": self.version_eval,
            "usage_effectiveness": self.usage_effectiveness,
            "conversion_confidence": round(self.conversion_confidence, 2),
            "overall": round(self.overall, 2),
            "status": self.status,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class SkillMaterialization:
    skill_id: str
    name: str
    version: str
    status: str
    candidate: SkillCandidate
    score: QualityScore
    source_title: str
    source_id: str
    conversion_id: str
    version_path: str = ""

    @property
    def source_sha256(self) -> str:
        return self.source_id


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    path: str = ""


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def add(self, severity: str, code: str, message: str, path: str = "") -> None:
        self.issues.append(ValidationIssue(severity, code, message, path))
