from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .evidence import EvidenceCatalog
from .models import Evidence, GroundedStatement, ProviderResult, ValidationReport
from .utils import normalize_excerpt, sha256_text


class EvidenceSelectionError(ValueError):
    """Fail-closed protocol error raised for an invalid Evidence selection."""


@dataclass(frozen=True)
class EvidenceScope:
    source_id: str
    conversion_id: str
    chunk_start_line: int | None = None
    chunk_end_line: int | None = None
    owned_start_line: int | None = None


@dataclass(frozen=True)
class EvidenceSelectionCandidate:
    index: int
    excerpt_hash: str
    locator: str
    excerpt: str = ""
    section: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "EvidenceSelectionCandidate":
        return cls(
            index=int(value.get("index", 0)),
            excerpt_hash=str(value.get("excerpt_hash", "")),
            locator=str(value.get("locator", "")),
            excerpt=str(value.get("excerpt", "")),
            section=str(value.get("section", "")),
        )

    def as_prompt_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "excerpt_hash": self.excerpt_hash,
            "locator": self.locator,
            "section": self.section,
            "excerpt": self.excerpt,
        }

    def as_portable_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "excerpt_hash": self.excerpt_hash,
            "locator": self.locator,
        }


@dataclass(frozen=True)
class EvidenceSelection:
    evidence: tuple[Evidence, ...]
    warnings: tuple[str, ...] = ()

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.evidence)


class EvidenceSelector:
    """Resolve system-issued indexes to immutable Evidence objects.

    Evidence IDs never cross the LLM protocol boundary.  The selector owns the
    deterministic index table and revalidates locator, conversion, chunk, and
    excerpt hash before returning the corresponding internal Evidence objects.
    """

    WARNING_SELECTION_COUNT = 20
    MAX_SELECTION_COUNT = 100

    def __init__(
        self,
        catalog: EvidenceCatalog,
        scope: EvidenceScope | None = None,
        candidates: Iterable[EvidenceSelectionCandidate | Mapping[str, object]] | None = None,
    ) -> None:
        self.catalog = catalog
        self.scope = scope or EvidenceScope(catalog.source_id, catalog.conversion_id)
        if self.scope.source_id != catalog.source_id or self.scope.conversion_id != catalog.conversion_id:
            raise EvidenceSelectionError("Evidence selector scope does not match its catalog.")
        generated = self._generate_candidates() if candidates is None else tuple(
            item if isinstance(item, EvidenceSelectionCandidate) else EvidenceSelectionCandidate.from_mapping(item)
            for item in candidates
        )
        indexes = [item.index for item in generated]
        if sorted(indexes) != list(range(1, len(indexes) + 1)):
            raise EvidenceSelectionError("Evidence candidate indexes must be contiguous unique positive integers.")
        self._candidates = tuple(sorted(generated, key=lambda item: item.index))
        self._candidate_by_index = {item.index: item for item in self._candidates}

    def _generate_candidates(self) -> tuple[EvidenceSelectionCandidate, ...]:
        ordered = sorted(
            self.catalog.items.values(),
            key=lambda item: (item.start_line, item.end_line, item.excerpt_sha256, item.evidence_id),
        )
        return tuple(
            EvidenceSelectionCandidate(
                index=index,
                excerpt_hash=item.excerpt_sha256,
                locator=item.locator,
                excerpt=item.excerpt,
                section=item.section,
            )
            for index, item in enumerate(ordered, start=1)
        )

    def prompt_candidates(self) -> list[dict[str, object]]:
        return [item.as_prompt_dict() for item in self._candidates]

    def portable_candidates(self) -> list[dict[str, object]]:
        return [item.as_portable_dict() for item in self._candidates]

    def validate(self, selected_evidence_indexes: Iterable[int]) -> EvidenceSelection:
        raw_indexes = list(selected_evidence_indexes)
        if not raw_indexes:
            raise EvidenceSelectionError("Source statement selected no Evidence index.")
        if len(raw_indexes) > self.MAX_SELECTION_COUNT:
            raise EvidenceSelectionError(
                f"Evidence selection count {len(raw_indexes)} exceeds hard limit {self.MAX_SELECTION_COUNT}."
            )
        warnings: list[str] = []
        if len(raw_indexes) > self.WARNING_SELECTION_COUNT:
            warnings.append(
                f"Evidence selection count {len(raw_indexes)} exceeds review threshold {self.WARNING_SELECTION_COUNT}."
            )
        selected: list[Evidence] = []
        seen: set[int] = set()
        for raw_index in raw_indexes:
            if isinstance(raw_index, bool) or not isinstance(raw_index, int):
                raise EvidenceSelectionError(f"Evidence index must be an integer: {raw_index!r}")
            candidate = self._candidate_by_index.get(raw_index)
            if candidate is None:
                raise EvidenceSelectionError(f"Evidence index does not exist in the current candidate list: {raw_index}")
            evidence = self._resolve(candidate)
            if raw_index in seen:
                if "Duplicate Evidence indexes were removed." not in warnings:
                    warnings.append("Duplicate Evidence indexes were removed.")
                continue
            seen.add(raw_index)
            selected.append(evidence)
        return EvidenceSelection(tuple(selected), tuple(warnings))

    def select(self, selected_evidence_indexes: Iterable[int]) -> tuple[str, ...]:
        """Compatibility convenience returning internal IDs after index validation."""
        return self.validate(selected_evidence_indexes).evidence_ids

    def indexes_for_evidence_ids(self, evidence_ids: Iterable[str]) -> tuple[int, ...]:
        """System-only conversion used when serializing a portable map payload."""
        by_id: dict[str, int] = {}
        for candidate in self._candidates:
            evidence = self._resolve(candidate)
            by_id[evidence.evidence_id] = candidate.index
        indexes: list[int] = []
        for raw in evidence_ids:
            evidence_id = str(raw).strip()
            index = by_id.get(evidence_id)
            if index is None:
                raise EvidenceSelectionError(f"System result references Evidence outside its candidate list: {evidence_id}")
            if index not in indexes:
                indexes.append(index)
        return tuple(indexes)

    def _resolve(self, candidate: EvidenceSelectionCandidate) -> Evidence:
        locator_matches = [item for item in self.catalog.items.values() if item.locator == candidate.locator]
        if not locator_matches:
            raise EvidenceSelectionError(f"Evidence candidate locator is not present in the current catalog: {candidate.index}")
        hash_matches = [item for item in locator_matches if item.excerpt_sha256 == candidate.excerpt_hash]
        if len(hash_matches) != 1:
            raise EvidenceSelectionError(f"Evidence hash mismatch for candidate index: {candidate.index}")
        evidence = hash_matches[0]
        actual_hash = sha256_text(normalize_excerpt(evidence.excerpt))
        if evidence.excerpt_sha256 != actual_hash or candidate.excerpt_hash != actual_hash:
            raise EvidenceSelectionError(f"Evidence hash mismatch for candidate index: {candidate.index}")
        if evidence.source_id != self.scope.source_id:
            raise EvidenceSelectionError(f"Evidence is outside the current source scope: {candidate.index}")
        if evidence.conversion_id != self.scope.conversion_id:
            raise EvidenceSelectionError(f"Evidence is outside the current conversion scope: {candidate.index}")
        if self.scope.chunk_start_line is not None and evidence.start_line < self.scope.chunk_start_line:
            raise EvidenceSelectionError(f"Evidence is outside the current chunk: {candidate.index}")
        if self.scope.chunk_end_line is not None and evidence.end_line > self.scope.chunk_end_line:
            raise EvidenceSelectionError(f"Evidence is outside the current chunk: {candidate.index}")
        if self.scope.owned_start_line is not None and evidence.start_line < self.scope.owned_start_line:
            raise EvidenceSelectionError(f"Evidence belongs to the preceding overlap owner: {candidate.index}")
        return evidence

    def validate_statement(self, statement: GroundedStatement) -> GroundedStatement:
        if statement.origin != "source":
            if statement.evidence_ids:
                raise EvidenceSelectionError("Engine-policy statements cannot bind source Evidence.")
            return statement
        indexes = self.indexes_for_evidence_ids(statement.evidence_ids)
        selection = self.validate(indexes)
        return GroundedStatement(statement.text, selection.evidence_ids, statement.origin)

    def validate_result(self, result: ProviderResult) -> ProviderResult:
        for candidate in result.all_candidates():
            for statement in candidate.all_statements():
                self.validate_statement(statement)
        return result

    def report(self, result: ProviderResult) -> ValidationReport:
        report = ValidationReport()
        try:
            self.validate_result(result)
        except EvidenceSelectionError as exc:
            report.add("error", "evidence-selection", str(exc))
        return report
