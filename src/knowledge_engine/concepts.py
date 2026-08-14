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
    disposition: str = "accepted"


@dataclass(frozen=True)
class ConceptQualityResult:
    accepted: tuple[ConceptCandidate, ...]
    rejected: tuple[ConceptCandidate, ...]
    quarantined: tuple[ConceptCandidate, ...]
    assessments: tuple[ConceptQualityAssessment, ...]
    duplicate_rate: float
    evidence_density: float
    chapter_coverage: float

    @property
    def passed(self) -> bool:
        return bool(self.accepted)

    @property
    def invalid_rate(self) -> float:
        total = len(self.accepted) + len(self.rejected)
        return 0.0 if total == 0 else round(100.0 * len(self.rejected) / total, 2)


@dataclass(frozen=True)
class BookQualityAssessment:
    passed: bool
    accepted_count: int
    quarantined_count: int
    rejected_count: int
    invalid_rate: float
    chapter_coverage: float
    systemic_blockers: tuple[str, ...]
    block_reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "passed": self.passed,
            "accepted_count": self.accepted_count,
            "quarantined_count": self.quarantined_count,
            "rejected_count": self.rejected_count,
            "invalid_rate": self.invalid_rate,
            "chapter_coverage": self.chapter_coverage,
            "systemic_blockers": list(self.systemic_blockers),
            "block_reason": self.block_reason,
        }


class BookKnowledgeGate:
    """Keep candidate quarantine separate from book-level fail-closed conditions."""

    MAX_INVALID_RATE = 80.0

    def validate(self, result: ConceptQualityResult) -> BookQualityAssessment:
        blockers: list[str] = []
        if not result.accepted:
            blockers.append("zero viable knowledge candidates")
        if result.evidence_density < 100.0:
            blockers.append("Evidence integrity is incomplete")
        if result.invalid_rate > self.MAX_INVALID_RATE:
            blockers.append(
                f"systemic candidate invalid rate {result.invalid_rate:.2f}% exceeds {self.MAX_INVALID_RATE:.2f}%"
            )
        reason = "; ".join(blockers)
        return BookQualityAssessment(
            passed=not blockers,
            accepted_count=len(result.accepted),
            quarantined_count=len(result.quarantined),
            rejected_count=len(result.rejected),
            invalid_rate=result.invalid_rate,
            chapter_coverage=result.chapter_coverage,
            systemic_blockers=tuple(blockers),
            block_reason=reason,
        )


class ConceptQualityGate:
    """Semantic fragment gate; title length alone never determines acceptance."""

    FRAGMENTS = {"则", "这就", "也就", "的就", "所以说", "就是说", "于是就", "然后就", "还有就是"}
    REAL_BOOK_TRUNCATED_PHRASES = {"学习只", "阿健同学正", "在生活中更"}
    TAIL_PARTICLE = re.compile(r"(?:的|地|得|就|也|而|且|但|却|呢|啊|不|都|在|和|与|或)$")
    CHAPTER_ONLY = re.compile(r"^(?:第[一二三四五六七八九十百0-9]+[章节篇部卷]|[一二三四五六七八九十]+[、.])$")
    SKILL_SIGNAL = re.compile(r"(?:方法|步骤|判断|检查|选择|执行|建立|分析|评估|核验|流程|原则|rule|process|method)", re.IGNORECASE)
    NARRATIVE_PREFIX = re.compile(
        r'^[\s"\'“”‘’]*(?:我|你|他|她|它|我们|你们|他们|这|那|如果|无论|因为|所以|但是|然而|'
        r'当然|报告显示|有人|朋友|先生|太太|公众应当|公众应该|读者应当|下一天|请看|告诉|为什么|问题不在于|对于|'
        r'当(?:我|你|他|她|它|我们|你们|他们))'
    )
    SENTENCE_PUNCTUATION = re.compile(r"[，。！？；：!?;:]")
    QUESTION_FRAGMENT = re.compile(r"(?:什么|为什么|怎么|怎样|哪(?:些|个)?|是否)$")
    ENGLISH_TRAILING_FRAGMENT = re.compile(r"\b(?:a|an|the|and|or|but|if|when|of|to|for|as|is|are|was|were)$", re.IGNORECASE)
    METADATA_SIGNAL = re.compile(
        r"(?:出版社|出版|印刷|邮编|网址|电话|书号|ISBN|版权|社址|集团地址|联系信息|开本|印张|定价|"
        r"反盗版热线|读者服务热线|广告经营许可证|图书在版编目|CIP数据|字数|版次|印次|"
        r"publisher|publication metadata|production credits|copyright-page|contact information)",
        re.IGNORECASE,
    )
    INVALID_TEXT = re.compile(r"\ufffd")

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
        quarantined: list[ConceptCandidate] = []
        for item in ordered:
            title = normalize_excerpt(item.title).strip("# ")
            definition = normalize_excerpt(item.definition.text)
            reasons: list[str] = []
            score = 100.0
            if not item.definition.evidence_ids:
                reasons.append("missing Evidence")
                score -= 50
            if not title or len(title) > 60:
                reasons.append("title is empty or too long for a stable concept label")
                score -= 80
            if self.INVALID_TEXT.search(title) or self.INVALID_TEXT.search(definition):
                reasons.append("text contains an undecodable replacement character")
                score -= 80
            if title in self.FRAGMENTS or title in self.REAL_BOOK_TRUNCATED_PHRASES or self.CHAPTER_ONLY.fullmatch(title):
                reasons.append("known discourse/chapter fragment")
                score -= 70
            if self.TAIL_PARTICLE.search(title) and not re.search(r"(?:力|率|论|法|性|值|熵)$", title):
                reasons.append("linguistically incomplete title")
                score -= 45
            if self.ENGLISH_TRAILING_FRAGMENT.search(title):
                reasons.append("English title ends with a discourse fragment")
                score -= 60
            if self.QUESTION_FRAGMENT.search(title):
                reasons.append("title ends as a narrative question rather than a concept label")
                score -= 70
            if self.NARRATIVE_PREFIX.search(title):
                reasons.append("title is narrative speech rather than a concept label")
                score -= 70
            if self.SENTENCE_PUNCTUATION.search(title):
                reasons.append("title contains sentence punctuation rather than label syntax")
                score -= 70
            if self.METADATA_SIGNAL.search(title + " " + definition):
                reasons.append("publication or converter metadata is not knowledge Evidence")
                score -= 90
            if definition and definition[-1:] in {"的", "地", "得", "就", "而", "与", "或", "：", ":"}:
                reasons.append("definition is a trailing sentence fragment")
                score -= 35
            if len(definition) < 4 and title == definition:
                reasons.append("no semantic definition beyond the label")
                score -= 35
            # Legitimate short concepts survive when their definition is complete and grounded.
            hard_reasons = {
                "known discourse/chapter fragment",
                "linguistically incomplete title",
                "title is narrative speech rather than a concept label",
                "title ends as a narrative question rather than a concept label",
                "title contains sentence punctuation rather than label syntax",
                "publication or converter metadata is not knowledge Evidence",
                "text contains an undecodable replacement character",
            }
            if (
                2 <= len(title) <= 4
                and len(definition) >= 6
                and item.definition.evidence_ids
                and title not in self.FRAGMENTS
                and not (hard_reasons & set(reasons))
            ):
                score = max(score, 75.0)
                reasons = [reason for reason in reasons if reason != "no semantic definition beyond the label"]
            skill_potential = bool(self.SKILL_SIGNAL.search(title + " " + definition))
            passed = score >= minimum_score
            disposition = "accepted" if passed else ("rejected" if not item.definition.evidence_ids else "quarantined")
            assessment = ConceptQualityAssessment(
                title, passed, max(0.0, score), tuple(reasons), skill_potential, disposition,
            )
            assessments.append(assessment)
            if assessment.passed:
                accepted.append(item)
            else:
                rejected.append(item)
                if disposition == "quarantined":
                    quarantined.append(item)
        return ConceptQualityResult(
            accepted=tuple(accepted), rejected=tuple(rejected), quarantined=tuple(quarantined), assessments=tuple(assessments),
            duplicate_rate=round(duplicate_rate * 100.0, 2), evidence_density=round(evidence_density, 2),
            chapter_coverage=round(chapter_coverage, 2),
        )

    def report(self, result: ConceptQualityResult) -> ValidationReport:
        report = ValidationReport()
        for assessment in result.assessments:
            if not assessment.passed:
                code = "concept-candidate-quarantined" if assessment.disposition == "quarantined" else "concept-candidate-rejected"
                report.add("warning", code, f"{assessment.title}: {', '.join(assessment.reasons)}")
        if result.evidence_density < 100:
            report.add("error", "concept-evidence-density", f"Concept Evidence density is {result.evidence_density}%; 100% is required.")
        return report
