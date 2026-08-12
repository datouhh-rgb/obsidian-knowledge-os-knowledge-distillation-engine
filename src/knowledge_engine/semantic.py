from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .evidence import EvidenceCatalog, EvidenceUniverse
from .models import GroundedStatement, SemanticAssessment, SkillCandidate, ValidationReport
from .utils import normalize_excerpt, now_iso, sha256_text


def statement_hash(statement: GroundedStatement) -> str:
    return sha256_text(statement.text)


@dataclass(frozen=True)
class SemanticGateResult:
    assessments: tuple[SemanticAssessment, ...]
    report: ValidationReport

    @property
    def status(self) -> str:
        statuses = {item.status for item in self.assessments}
        if "unsupported" in statuses:
            return "failed"
        if statuses & {"partial", "conflict"}:
            return "review"
        return "passed"


class SemanticSupportGate:
    """Deterministic conservative support check; never claims full semantic proof."""

    VERSION = "lexical-entailment-v1"

    def assess_skill(self, candidate: SkillCandidate, catalog: EvidenceCatalog) -> SemanticGateResult:
        return self.assess(candidate.all_statements(), EvidenceUniverse.from_catalogs([catalog]))

    def assess(self, statements: Iterable[GroundedStatement], universe: EvidenceUniverse) -> SemanticGateResult:
        report = ValidationReport()
        assessments: list[SemanticAssessment] = []
        for index, statement in enumerate(statements, start=1):
            if statement.origin == "engine_policy":
                assessments.append(SemanticAssessment(
                    statement_hash(statement), "supported", "Engine policy is explicitly labeled and is not represented as source-derived.",
                    (), self.VERSION, now_iso(),
                ))
                continue
            evidence = [universe.get(item) for item in statement.evidence_ids]
            if not evidence or any(item is None for item in evidence):
                status = "unsupported"
                reason = "One or more referenced Evidence records are missing."
            else:
                excerpts = [normalize_excerpt(item.excerpt) for item in evidence if item is not None]
                status, reason = self._classify(statement.text, excerpts)
            assessment = SemanticAssessment(
                statement_hash(statement), status, reason, statement.evidence_ids, self.VERSION, now_iso(),
            )
            assessments.append(assessment)
            if status == "unsupported":
                report.add("error", "semantic-unsupported", reason, f"statement:{index}")
            elif status in {"partial", "conflict"}:
                report.add("warning", f"semantic-{status}", reason, f"statement:{index}")
        return SemanticGateResult(tuple(assessments), report)

    @classmethod
    def _classify(cls, statement: str, excerpts: list[str]) -> tuple[str, str]:
        normalized_statement = normalize_excerpt(statement)
        joined = " ".join(excerpts)
        if not normalized_statement:
            return "unsupported", "Statement is empty."
        if cls._explicit_conflict(excerpts):
            return "conflict", "Bound Evidence contains explicit opposing polarity and requires human conflict review."
        if normalized_statement.casefold() in joined.casefold():
            return "supported", "The normalized statement is present verbatim in bound Evidence."
        statement_tokens = cls._tokens(normalized_statement)
        evidence_tokens = cls._tokens(joined)
        if not statement_tokens:
            return "unsupported", "Statement has no comparable semantic tokens."
        coverage = len(statement_tokens & evidence_tokens) / len(statement_tokens)
        if cls._evidence_bound_procedural_abstraction(normalized_statement, joined, coverage):
            return "supported", (
                "Evidence-bound procedural abstraction passed: at least three atomic source signals are "
                f"present in the bound Evidence and lexical support coverage is {coverage:.2f}."
            )
        if coverage >= 0.72:
            return "supported", f"Conservative lexical support coverage is {coverage:.2f}."
        if coverage >= 0.35:
            return "partial", f"Only partial lexical support coverage ({coverage:.2f}); human semantic review is required."
        return "unsupported", f"Lexical support coverage ({coverage:.2f}) is below the production threshold."

    @staticmethod
    def _tokens(value: str) -> set[str]:
        value = value.casefold()
        words = set(re.findall(r"[a-z0-9]{2,}", value))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", value))
        words.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
        return {item for item in words if item}

    @staticmethod
    def _explicit_conflict(excerpts: list[str]) -> bool:
        affirmative = any(marker in item.casefold() for item in excerpts for marker in ("must ", "必须", "应当", "always "))
        negative = any(marker in item.casefold() for item in excerpts for marker in ("must not", "不得", "禁止", "never "))
        return affirmative and negative

    @staticmethod
    def _evidence_bound_procedural_abstraction(statement: str, evidence: str, coverage: float) -> bool:
        signal_match = re.search(
            r"(?:原文信号|source signals?)[“\"]([^”\"]+)[”\"]",
            statement,
            re.IGNORECASE,
        )
        signals = [] if signal_match is None else [
            normalize_excerpt(item).strip()
            for item in re.split(r"[、,，]|\band\b", signal_match.group(1), flags=re.IGNORECASE)
            if normalize_excerpt(item).strip()
        ]
        exact_signals = (
            len(set(signals)) >= 3
            and all(2 <= len(item) <= 8 and item.casefold() in evidence.casefold() for item in signals)
        )
        condition = bool(re.search(r"(?:当|若|如果|一旦|when|if).{1,100}(?:时|then|,|，)", statement, re.IGNORECASE))
        observable = bool(re.search(r"(?:记录|输出|生成|保存|record|output|return)", statement, re.IGNORECASE))
        stop = bool(re.search(r"(?:停止|终止|拒绝|stop|block)", statement, re.IGNORECASE))
        return exact_signals and condition and observable and stop and coverage >= 0.05
