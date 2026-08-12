from __future__ import annotations

from typing import Any, Iterable

from .evidence import EvidenceCatalog, EvidenceGate
from .gates import ProceduralActionGate, SkillExecutabilityGate, SkillStructureGate
from .models import ConceptConflictCandidate, ProviderResult, QualityScore, SkillCandidate, ValidationReport


class SkillProductionGate:
    """Last fail-closed gate before Registry materialization."""

    def __init__(self, minimum_quality: float = 70.0) -> None:
        self.minimum_quality = minimum_quality
        self.structure = SkillStructureGate()
        self.evidence = EvidenceGate()
        self.executability = SkillExecutabilityGate()
        self.procedural = ProceduralActionGate()

    def validate(
        self,
        candidate: SkillCandidate,
        catalog: EvidenceCatalog,
        *,
        concepts_stable: bool,
        graph_dry_run: Any,
        conflicts: Iterable[ConceptConflictCandidate] = (),
        quality_score: QualityScore | float | None = None,
    ) -> ValidationReport:
        report = ValidationReport()
        report.issues.extend(self.structure.validate(candidate).issues)
        report.issues.extend(self.evidence.validate(ProviderResult(provider=candidate.provider, skills=[candidate]), catalog).issues)
        report.issues.extend(self.executability.validate(candidate).issues)
        report.issues.extend(self.procedural.validate(candidate, catalog).issues)
        if candidate.provider.casefold() == "rule" or candidate.provider.casefold().startswith("rule:"):
            report.add("error", "rule-analyzer-diagnostic-only", "Rule Analyzer cannot produce a production Skill.", candidate.title)
        if not concepts_stable:
            report.add("error", "unstable-concepts", "Skill production requires stable canonical concepts.", candidate.title)
        unresolved = [item for item in conflicts if item.status == "unresolved"]
        if unresolved:
            report.add("error", "unresolved-concept-conflict", f"{len(unresolved)} unresolved concept conflict(s) block Skill production.", candidate.title)
        graph_passed = bool(getattr(graph_dry_run, "passed", graph_dry_run is True))
        if not graph_passed:
            report.add("error", "graph-dry-run", "Knowledge Graph dry-run did not pass.", candidate.title)
        if quality_score is not None:
            value = float(quality_score.overall if isinstance(quality_score, QualityScore) else quality_score)
            if value < self.minimum_quality:
                report.add("error", "skill-quality-threshold", f"Skill quality {value:.2f} is below {self.minimum_quality:.2f}.", candidate.title)
            if isinstance(quality_score, QualityScore) and quality_score.status == "blocked":
                report.add("error", "skill-quality-blocked", "Skill quality evaluator marked this candidate blocked.", candidate.title)
        if not candidate.source_statements():
            report.add("error", "skill-source-evidence", "Skill has no source-origin statement and cannot be produced.", candidate.title)
        return report

    def require(self, *args: Any, **kwargs: Any) -> None:
        report = self.validate(*args, **kwargs)
        if report.errors:
            first = report.errors[0]
            raise ValueError(f"Skill production gate failed: {first.code}: {first.message}")
