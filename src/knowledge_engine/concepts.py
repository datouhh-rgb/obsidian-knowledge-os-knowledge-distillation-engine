from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from .models import (
    CanonicalConcept,
    ConceptCandidate,
    ConceptConflictCandidate,
    GroundedStatement,
    ValidationReport,
)
from .utils import canonical_json, normalize_excerpt, sha256_text


@dataclass(frozen=True)
class CanonicalizationResult:
    concepts: tuple[CanonicalConcept, ...]
    conflicts: tuple[ConceptConflictCandidate, ...]

    @property
    def candidates(self) -> list[ConceptCandidate]:
        return [item.as_candidate() for item in self.concepts]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "concepts": [item.as_dict() for item in self.concepts],
            "conflicts": [item.as_dict() for item in self.conflicts],
        }


class ConceptCanonicalizer:
    """Deterministically merge formatting variants without hiding homonyms."""

    VERSION = "concept-canonicalizer-v1"
    NEGATIVE = re.compile(r"(?:不|无|非|禁止|不得|不能|never|not|without)", re.IGNORECASE)
    POSITIVE = re.compile(r"(?:必须|应当|可以|增加|允许|must|should|allow)", re.IGNORECASE)

    def canonicalize(self, candidates: Iterable[ConceptCandidate], source_id: str) -> CanonicalizationResult:
        grouped: dict[str, list[ConceptCandidate]] = {}
        for candidate in candidates:
            grouped.setdefault(self.normalized_title(candidate.title), []).append(candidate)
        concepts: list[CanonicalConcept] = []
        conflicts: list[ConceptConflictCandidate] = []
        for normalized_title in sorted(grouped):
            group = sorted(grouped[normalized_title], key=self._candidate_key)
            clusters: list[list[ConceptCandidate]] = []
            for candidate in group:
                compatible = next((cluster for cluster in clusters if self._compatible(cluster[0], candidate)), None)
                if compatible is None:
                    clusters.append([candidate])
                else:
                    compatible.append(candidate)
            if len(clusters) > 1:
                conflict_payload = {
                    "source_id": source_id,
                    "title": normalized_title,
                    "definitions": sorted(normalize_excerpt(item.definition.text) for item in group),
                }
                conflicts.append(ConceptConflictCandidate(
                    conflict_id="concept-conflict-" + sha256_text(canonical_json(conflict_payload))[:24],
                    normalized_title=normalized_title,
                    candidates=tuple(group),
                    reason="same-title semantic conflict; automatic merge refused",
                ))
            for cluster_index, cluster in enumerate(clusters):
                concepts.append(self._canonical(cluster, source_id, normalized_title, cluster_index if len(clusters) > 1 else None))
        return CanonicalizationResult(
            concepts=tuple(sorted(concepts, key=lambda item: item.canonical_concept_id)),
            conflicts=tuple(sorted(conflicts, key=lambda item: item.conflict_id)),
        )

    @classmethod
    def normalized_title(cls, value: str) -> str:
        value = normalize_excerpt(value).strip("#*-—–:：;；,.，。 ")
        if "：" in value or ":" in value:
            prefix = re.split(r"[：:]", value, maxsplit=1)[0].strip()
            if 1 < len(prefix) <= 60:
                value = prefix
        value = re.sub(r"[\s_\-—–·•]+", "", value)
        value = value.replace("（", "(").replace("）", ")")
        return value.casefold()

    def _canonical(self, cluster: list[ConceptCandidate], source_id: str, normalized_title: str, semantic_index: int | None) -> CanonicalConcept:
        aliases = sorted({normalize_excerpt(item.title) for item in cluster})
        mentions = sorted({normalize_excerpt(item.definition.text) for item in cluster})
        evidence_ids = sorted({evidence_id for item in cluster for evidence_id in item.definition.evidence_ids})
        representative = sorted(cluster, key=lambda item: (-len(normalize_excerpt(item.definition.text)), self._candidate_key(item)))[0]
        title = self._display_title(aliases, normalized_title)
        identity_payload = {
            "source_id": source_id,
            "canonical_title": normalized_title,
            "semantic_cluster": semantic_index,
            "definitions": mentions if semantic_index is not None else [],
            "version": self.VERSION,
        }
        reason = "single stable mention" if len(cluster) == 1 else "format/pagination/prefix-equivalent mentions merged"
        definition = GroundedStatement(representative.definition.text, tuple(evidence_ids), representative.definition.origin)
        return CanonicalConcept(
            canonical_concept_id="concept:" + sha256_text(canonical_json(identity_payload))[:24],
            canonical_title=title,
            aliases=tuple(aliases),
            source_mentions=tuple(mentions),
            evidence_ids=tuple(evidence_ids),
            merge_reason=reason,
            confidence=round(sum(item.confidence for item in cluster) / len(cluster), 4),
            definition=definition,
            provider="+".join(sorted({item.provider for item in cluster})),
        )

    @staticmethod
    def _display_title(aliases: list[str], normalized: str) -> str:
        bases = [re.split(r"[：:]", item, maxsplit=1)[0].strip() for item in aliases]
        exact = [item for item in bases if ConceptCanonicalizer.normalized_title(item) == normalized]
        return sorted(exact or bases, key=lambda item: (len(item), item.casefold()))[0]

    def _compatible(self, left: ConceptCandidate, right: ConceptCandidate) -> bool:
        one = normalize_excerpt(left.definition.text).casefold()
        two = normalize_excerpt(right.definition.text).casefold()
        if one == two or one.startswith(two) or two.startswith(one):
            return True
        negation_conflict = bool(self.NEGATIVE.search(one)) != bool(self.NEGATIVE.search(two))
        polarity_conflict = negation_conflict and bool(self.POSITIVE.search(one + " " + two))
        similarity = SequenceMatcher(None, one, two).ratio()
        if polarity_conflict:
            return False
        return similarity >= 0.72

    @staticmethod
    def _candidate_key(candidate: ConceptCandidate) -> str:
        return canonical_json({
            "title": normalize_excerpt(candidate.title).casefold(),
            "text": normalize_excerpt(candidate.definition.text).casefold(),
            "evidence": sorted(candidate.definition.evidence_ids),
            "provider": candidate.provider,
        })


@dataclass(frozen=True)
class ConceptQualityAssessment:
    title: str
    passed: bool
    score: float
    reasons: tuple[str, ...]
    skill_potential: bool


@dataclass(frozen=True)
class ConceptQualityResult:
    accepted: tuple[ConceptCandidate, ...]
    rejected: tuple[ConceptCandidate, ...]
    assessments: tuple[ConceptQualityAssessment, ...]
    duplicate_rate: float
    evidence_density: float
    chapter_coverage: float

    @property
    def passed(self) -> bool:
        return bool(self.accepted)


class ConceptQualityGate:
    """Semantic fragment gate; title length alone never determines acceptance."""

    FRAGMENTS = {"这就", "也就", "的就", "所以说", "就是说", "于是就", "然后就", "还有就是"}
    TAIL_PARTICLE = re.compile(r"(?:的|地|得|就|也|而|且|但|却|呢|啊)$")
    CHAPTER_ONLY = re.compile(r"^(?:第[一二三四五六七八九十百0-9]+[章节篇部卷]|[一二三四五六七八九十]+[、.])$")
    SKILL_SIGNAL = re.compile(r"(?:方法|步骤|判断|检查|选择|执行|建立|分析|评估|核验|流程|原则|rule|process|method)", re.IGNORECASE)

    def validate(
        self,
        candidates: Iterable[ConceptCandidate],
        chapter_titles: Iterable[str] = (),
        minimum_score: float = 60.0,
    ) -> ConceptQualityResult:
        ordered = sorted(candidates, key=lambda item: (ConceptCanonicalizer.normalized_title(item.title), item.definition.text))
        normalized_titles = [ConceptCanonicalizer.normalized_title(item.title) for item in ordered]
        unique_count = len(set(normalized_titles))
        duplicate_rate = 0.0 if not ordered else 1.0 - unique_count / len(ordered)
        chapters = {ConceptCanonicalizer.normalized_title(item) for item in chapter_titles if str(item).strip()}
        covered = {title for title in normalized_titles if any(chapter and chapter in title for chapter in chapters)}
        chapter_coverage = 100.0 if not chapters else 100.0 * len(covered) / len(chapters)
        evidence_density = 0.0 if not ordered else sum(bool(item.definition.evidence_ids) for item in ordered) / len(ordered) * 100.0
        assessments: list[ConceptQualityAssessment] = []
        accepted: list[ConceptCandidate] = []
        rejected: list[ConceptCandidate] = []
        for item in ordered:
            title = normalize_excerpt(item.title).strip("# ")
            definition = normalize_excerpt(item.definition.text)
            reasons: list[str] = []
            score = 100.0
            if not item.definition.evidence_ids:
                reasons.append("missing Evidence")
                score -= 50
            if title in self.FRAGMENTS or self.CHAPTER_ONLY.fullmatch(title):
                reasons.append("known discourse/chapter fragment")
                score -= 70
            if self.TAIL_PARTICLE.search(title) and not re.search(r"(?:力|率|论|法|性|值|熵)$", title):
                reasons.append("linguistically incomplete title")
                score -= 45
            if definition and definition[-1:] in {"的", "地", "得", "就", "而", "与", "或", "：", ":"}:
                reasons.append("definition is a trailing sentence fragment")
                score -= 35
            if len(definition) < 4 and title == definition:
                reasons.append("no semantic definition beyond the label")
                score -= 35
            # Legitimate short concepts survive when their definition is complete and grounded.
            if len(title) <= 4 and len(definition) >= 6 and item.definition.evidence_ids and title not in self.FRAGMENTS:
                score = max(score, 75.0)
                reasons = [reason for reason in reasons if reason != "no semantic definition beyond the label"]
            skill_potential = bool(self.SKILL_SIGNAL.search(title + " " + definition))
            assessment = ConceptQualityAssessment(title, score >= minimum_score, max(0.0, score), tuple(reasons), skill_potential)
            assessments.append(assessment)
            (accepted if assessment.passed else rejected).append(item)
        return ConceptQualityResult(
            accepted=tuple(accepted), rejected=tuple(rejected), assessments=tuple(assessments),
            duplicate_rate=round(duplicate_rate * 100.0, 2), evidence_density=round(evidence_density, 2),
            chapter_coverage=round(chapter_coverage, 2),
        )

    def report(self, result: ConceptQualityResult) -> ValidationReport:
        report = ValidationReport()
        for assessment in result.assessments:
            if not assessment.passed:
                report.add("warning", "concept-fragment-rejected", f"{assessment.title}: {', '.join(assessment.reasons)}")
        if result.evidence_density < 100:
            report.add("error", "concept-evidence-density", f"Concept Evidence density is {result.evidence_density}%; 100% is required.")
        return report
