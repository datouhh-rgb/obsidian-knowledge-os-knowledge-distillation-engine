from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable

from .evidence import EvidenceCatalog, EvidenceGate
from .gates import SkillExecutabilityGate, SkillStructureGate
from .models import (
    CanonicalConcept,
    GroundedStatement,
    MethodCluster,
    ProviderResult,
    SkillCandidate,
    ValidationReport,
)
from .utils import canonical_json, normalize_excerpt, sha256_text


@dataclass(frozen=True)
class SkillSynthesisOutcome:
    candidate: SkillCandidate | None
    validation: ValidationReport


@dataclass
class KnowledgeSynthesisResult:
    method_clusters: list[MethodCluster] = field(default_factory=list)
    skills: list[SkillCandidate] = field(default_factory=list)
    skill_method_ids: dict[str, str] = field(default_factory=dict)
    blocked: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "method_clusters": [item.as_dict() for item in self.method_clusters],
            "skills": [
                {
                    "fingerprint": item.fingerprint(),
                    "method_cluster_id": self.skill_method_ids[item.fingerprint()],
                    "candidate": item.semantic_payload(),
                }
                for item in self.skills
            ],
            "blocked": self.blocked,
        }


@dataclass(frozen=True)
class _ConceptProfile:
    concept: CanonicalConcept
    tokens: frozenset[str]
    sections: frozenset[str]
    actions: frozenset[str]
    purpose: str
    purpose_label: str
    title_signal: int


class SkillSynthesisGate:
    """Additional V3.3 gate; existing production gates remain authoritative."""

    ACTIONABLE = re.compile(r"(?:如何|方法|流程|判断|分析|评估|控制|复盘|执行|选择|决策|检查|应用)")

    def __init__(self) -> None:
        self.structure = SkillStructureGate()
        self.evidence = EvidenceGate()
        self.executability = SkillExecutabilityGate()

    def validate(
        self,
        cluster: MethodCluster | None,
        candidate: SkillCandidate | None,
        catalog: EvidenceCatalog,
    ) -> ValidationReport:
        report = ValidationReport()
        if cluster is None:
            report.add("error", "skill-synthesis-method-required", "Skill synthesis requires a Method Cluster.")
            return report
        if len(cluster.concept_ids) < 2:
            report.add("error", "method-cluster-concepts", "A Method Cluster requires at least two concepts.", cluster.id)
        if len(cluster.evidence_ids) < 3:
            report.add("error", "method-cluster-evidence", "A Method Cluster requires at least three Evidence records.", cluster.id)
        for evidence_id in cluster.evidence_ids:
            evidence = catalog.get(evidence_id)
            if evidence is None:
                report.add("error", "method-cluster-unknown-evidence", f"Unknown Evidence: {evidence_id}", cluster.id)
            elif evidence.source_id != catalog.source_id or evidence.conversion_id != catalog.conversion_id:
                report.add("error", "method-cluster-evidence-scope", f"Evidence outside active conversion: {evidence_id}", cluster.id)
        if candidate is None:
            report.add("error", "skill-synthesis-candidate-missing", "Method Cluster did not produce a Skill Candidate.", cluster.id)
            return report
        report.issues.extend(self.structure.validate(candidate).issues)
        report.issues.extend(self.evidence.validate(ProviderResult(provider=candidate.provider, skills=[candidate]), catalog).issues)
        report.issues.extend(self.executability.validate(candidate).issues)
        bound = {item for statement in candidate.source_statements() for item in statement.evidence_ids}
        if len(bound) < 3:
            report.add("error", "synthesized-skill-evidence", "A synthesized Skill requires at least three Evidence bindings.", candidate.title)
        if not bound.issubset(set(cluster.evidence_ids)):
            report.add("error", "synthesized-skill-method-scope", "Skill Evidence must be inherited from its Method Cluster.", candidate.title)
        if not self.ACTIONABLE.search(candidate.title):
            report.add("error", "synthesized-skill-summary", "Skill title must describe an actionable method, not a knowledge explanation.", candidate.title)
        return report


class SkillSynthesizer:
    """Convert one valid Method Cluster into one review-only Skill Candidate."""

    VERSION = "skill-synthesizer-v1"

    def __init__(self) -> None:
        self.gate = SkillSynthesisGate()

    def synthesize(self, cluster: MethodCluster | None, catalog: EvidenceCatalog) -> SkillSynthesisOutcome:
        if cluster is None:
            validation = self.gate.validate(None, None, catalog)
            return SkillSynthesisOutcome(None, validation)
        candidate: SkillCandidate | None = None
        if len(cluster.concept_ids) >= 2 and len(cluster.evidence_ids) >= 3:
            selected = [catalog.get(item) for item in cluster.evidence_ids]
            evidence = [item for item in selected if item is not None][:4]
            actions = tuple(
                GroundedStatement(item.excerpt, (item.evidence_id,), "source")
                for item in evidence
            )

            def policy(text: str) -> GroundedStatement:
                return GroundedStatement(text, (), "engine_policy")

            candidate = SkillCandidate(
                title=cluster.title,
                positioning=policy(f"Apply {cluster.title} as an Evidence-bound decision procedure."),
                scenarios=(policy("Use when the task matches the Method Cluster purpose and the required source facts are available."),),
                input_requirements=(
                    policy("Provide the decision target, current constraints, and the relevant observable facts."),
                    policy("Provide or confirm access to the bound source Evidence before execution."),
                ),
                decision_flow=(
                    policy("Confirm that the request matches the Method Cluster purpose and boundaries."),
                    policy("Compare the Evidence-bound concept signals and identify agreement, conflict, and missing inputs."),
                    policy("Stop for human review when Evidence conflicts or critical inputs remain missing."),
                ),
                actions=actions,
                output_format=policy("Return the decision context, Evidence observations, actions, boundaries, and unresolved review items."),
                boundaries=(
                    policy("Do not generalize beyond the bound source books, conversion, and Evidence."),
                    policy("Keep the output in candidate status until explicit human review is complete."),
                ),
                prohibited_scenarios=(
                    policy("Do not use when fewer than three valid Evidence records support the Method Cluster."),
                    policy("Do not auto-approve, auto-expose, or connect the candidate to a project."),
                ),
                provider=f"synthesis:{self.VERSION}",
                confidence=cluster.confidence,
            )
        validation = self.gate.validate(cluster, candidate, catalog)
        return SkillSynthesisOutcome(candidate if validation.passed else None, validation)


class KnowledgeSynthesizer:
    """Cluster stable concepts using semantic, Evidence, chapter, purpose, and action signals."""

    VERSION = "knowledge-synthesizer-v1"
    MAX_CLUSTERS = 8
    MAX_CONCEPTS_PER_CLUSTER = 6
    PURPOSES = (
        ("assessment", "分析判断", ("判断", "评估", "分析", "识别", "观察", "比较", "验证", "核验", "趋势", "反馈", "信号", "概率", "可能性")),
        ("risk", "风险控制", ("控制", "风险", "纪律", "约束", "止损", "避免", "禁止", "限制", "安全", "损失", "亏损", "危险", "破产", "保护", "下跌")),
        ("review", "复盘改进", ("复盘", "回顾", "记录", "总结", "反馈", "修正", "改进", "检查", "经验", "教训", "思考", "历史", "研究", "学习")),
        ("planning", "计划准备", ("计划", "准备", "策略", "规则", "目标", "条件", "时机", "等待", "耐心", "预期", "机会", "前提")),
        ("execution", "执行行动", ("执行", "行动", "操作", "交易", "买入", "买进", "卖出", "调整", "退出", "进入", "持有")),
        ("decision", "决策选择", ("选择", "决策", "取舍", "确认", "拒绝", "接受", "决定", "决断", "进场", "离场")),
    )
    PURPOSE_FAMILIES = (
        ("decision-cycle", "决策分析", ("assessment", "planning", "decision")),
        ("learning-cycle", "复盘规划", ("review", "planning")),
        ("risk-action", "风险执行", ("risk", "execution")),
    )
    ACTIONS = re.compile(r"(?:判断|评估|分析|识别|观察|比较|验证|控制|约束|停止|等待|记录|复盘|检查|选择|执行|调整|买入|买进|卖出|退出|进入|持有|思考|总结|研究|学习|预期|保护)")
    FRAGMENT_PREFIX = re.compile(r"^(?:我|你|他|她|它|我们|你们|他们|这|那|无论|如果|因为|所以|但是|而且|虽然|当时|其实|当然|也许|报告显示)")
    FRAGMENT_SUFFIX = re.compile(r"(?:这|那|的|了|着|过|而|但|却|就|还|又|时|后|前|中)$")

    def __init__(self, skill_synthesizer: SkillSynthesizer | None = None) -> None:
        self.skill_synthesizer = skill_synthesizer or SkillSynthesizer()

    def synthesize(
        self,
        concepts: Iterable[CanonicalConcept],
        catalog: EvidenceCatalog,
        source_book: str,
    ) -> KnowledgeSynthesisResult:
        result = KnowledgeSynthesisResult()
        profiles = [profile for item in concepts if (profile := self._profile(item, catalog)) is not None]
        grouped: dict[str, list[_ConceptProfile]] = {}
        for profile in profiles:
            grouped.setdefault(profile.purpose, []).append(profile)
        candidates: list[tuple[float, MethodCluster]] = []
        synthesis_groups = [
            (purpose, values[0].purpose_label, values)
            for purpose, values in sorted(grouped.items())
        ]
        synthesis_groups.extend(
            (family, label, [item for item in profiles if item.purpose in members])
            for family, label, members in self.PURPOSE_FAMILIES
        )
        for purpose, purpose_label, purpose_profiles in synthesis_groups:
            if not purpose_profiles:
                continue
            by_chapter: dict[str, list[_ConceptProfile]] = {}
            for profile in purpose_profiles:
                chapter = sorted(profile.sections)[0] if profile.sections else ""
                by_chapter.setdefault(chapter, []).append(profile)
            chapter_groups = [items for items in by_chapter.values() if len(items) >= 3]
            groups = chapter_groups or [purpose_profiles]
            for group_index, values in enumerate(sorted(groups, key=lambda items: (-len(items), self._profile_rank(sorted(items, key=self._profile_rank)[0])))):
                group = sorted(values, key=self._profile_rank)[: self.MAX_CONCEPTS_PER_CLUSTER]
                cluster, reasons = self._cluster(group, source_book, purpose, purpose_label)
                if cluster is None:
                    result.blocked.append({
                        "purpose": purpose,
                        "group": group_index,
                        "concept_ids": [item.concept.canonical_concept_id for item in group],
                        "reasons": reasons,
                    })
                    continue
                candidates.append((cluster.confidence, cluster))
        selected_clusters: list[MethodCluster] = []
        for _score, cluster in sorted(candidates, key=lambda item: (-item[0], item[1].id)):
            members = set(cluster.concept_ids)
            if any(
                len(members & set(prior.concept_ids)) / len(members | set(prior.concept_ids)) >= 0.75
                for prior in selected_clusters
            ):
                result.blocked.append({"method_cluster_id": cluster.id, "reasons": ["duplicate Method Cluster overlap >= 0.75"]})
                continue
            selected_clusters.append(cluster)
            if len(selected_clusters) >= self.MAX_CLUSTERS:
                break
        for cluster in selected_clusters:
            result.method_clusters.append(cluster)
            outcome = self.skill_synthesizer.synthesize(cluster, catalog)
            if outcome.candidate is None:
                result.blocked.append({
                    "method_cluster_id": cluster.id,
                    "reasons": [f"{item.code}: {item.message}" for item in outcome.validation.errors],
                })
                continue
            result.skills.append(outcome.candidate)
            result.skill_method_ids[outcome.candidate.fingerprint()] = cluster.id
        return result

    def _profile(self, concept: CanonicalConcept, catalog: EvidenceCatalog) -> _ConceptProfile | None:
        evidence = [catalog.get(item) for item in concept.evidence_ids]
        valid = [item for item in evidence if item is not None]
        if not valid or len(valid) != len(concept.evidence_ids):
            return None
        title = normalize_excerpt(concept.canonical_title).strip()
        if (
            len(title) < 3 or len(title) > 40
            or self.FRAGMENT_PREFIX.search(title)
            or self.FRAGMENT_SUFFIX.search(title)
        ):
            return None
        title_signal = sum(
            title.count(keyword)
            for _key, _label, keywords in self.PURPOSES
            for keyword in keywords
        )
        text = normalize_excerpt(title + " " + concept.definition.text)
        purpose_scores = [
            (sum(text.count(keyword) for keyword in keywords), key, label)
            for key, label, keywords in self.PURPOSES
        ]
        score, purpose, purpose_label = max(purpose_scores, key=lambda item: (item[0], -self._purpose_order(item[1])))
        if score <= 0:
            return None
        return _ConceptProfile(
            concept=concept,
            tokens=frozenset(self._tokens(text)),
            sections=frozenset(normalize_excerpt(item.section).casefold() for item in valid if item.section.strip()),
            actions=frozenset(self.ACTIONS.findall(text)),
            purpose=purpose,
            purpose_label=purpose_label,
            title_signal=title_signal,
        )

    def _cluster(
        self,
        profiles: list[_ConceptProfile],
        source_book: str,
        purpose: str,
        purpose_label: str,
    ) -> tuple[MethodCluster | None, list[str]]:
        reasons: list[str] = []
        if len(profiles) < 2:
            return None, ["fewer than two stable concepts"]
        evidence_ids = sorted({item for profile in profiles for item in profile.concept.evidence_ids})
        if len(evidence_ids) < 3:
            return None, ["fewer than three unique Evidence records"]
        pairs = list(combinations(profiles, 2))
        semantic = sum(self._jaccard(left.tokens, right.tokens) for left, right in pairs) / max(len(pairs), 1)
        chapter_pairs = sum(bool(left.sections & right.sections) for left, right in pairs)
        action_overlap = sum(self._jaccard(left.actions, right.actions) for left, right in pairs) / max(len(pairs), 1)
        dimensions = 2 + int(semantic >= 0.03) + int(chapter_pairs > 0) + int(action_overlap > 0)
        if dimensions < 3:
            reasons.append("insufficient multi-signal cohesion")
            return None, reasons
        confidence = min(0.98, (
            0.35
            + 0.20 * min(1.0, semantic * 4)
            + 0.20 * chapter_pairs / max(len(pairs), 1)
            + 0.15 * min(1.0, action_overlap * 3)
            + 0.10 * min(1.0, len(evidence_ids) / 5)
        ))
        selected = sorted(profiles, key=self._profile_rank)
        actions = Counter(action for item in selected for action in item.actions)
        terms = [item for item, _count in actions.most_common(2)]
        fallbacks = [purpose_label[:2], purpose_label[2:]]
        for fallback in fallbacks:
            if fallback and fallback not in terms:
                terms.append(fallback)
            if len(terms) >= 2:
                break
        method_title = f"{terms[0]}与{terms[1]}的{purpose_label}方法"
        method_title = method_title[:96].rstrip("与的")
        concept_ids = tuple(sorted(item.concept.canonical_concept_id for item in profiles))
        creation_reason = (
            f"semantic_similarity={semantic:.3f}; shared_evidence_source={source_book}; "
            f"shared_chapter_pairs={chapter_pairs}/{len(pairs)}; decision_purpose={purpose}; "
            f"action_flow_overlap={action_overlap:.3f}; concepts={len(concept_ids)}; evidence={len(evidence_ids)}"
        )
        identity = canonical_json({
            "version": self.VERSION,
            "source_books": [source_book],
            "concept_ids": concept_ids,
            "evidence_ids": evidence_ids,
            "purpose": purpose,
        })
        cluster = MethodCluster(
            id="method-cluster:" + sha256_text(identity)[:24],
            title=method_title,
            description=f"将 {len(concept_ids)} 个稳定 Concept 聚合为面向{purpose_label}的可审查方法候选。",
            concept_ids=concept_ids,
            evidence_ids=tuple(evidence_ids),
            source_books=(source_book,),
            confidence=round(confidence, 4),
            creation_reason=creation_reason,
        )
        return cluster, reasons

    @classmethod
    def _purpose_order(cls, purpose: str) -> int:
        return next((index for index, item in enumerate(cls.PURPOSES) if item[0] == purpose), len(cls.PURPOSES))

    @staticmethod
    def _profile_rank(profile: _ConceptProfile) -> tuple[int, float, int, str]:
        return (
            -profile.title_signal,
            -profile.concept.confidence,
            -len(profile.concept.evidence_ids),
            profile.concept.canonical_concept_id,
        )

    @staticmethod
    def _tokens(value: str) -> set[str]:
        lowered = value.casefold()
        tokens = set(re.findall(r"[a-z0-9]{2,}", lowered))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
        tokens.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
        return {item for item in tokens if item}

    @staticmethod
    def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
        union = left | right
        return 0.0 if not union else len(left & right) / len(union)
