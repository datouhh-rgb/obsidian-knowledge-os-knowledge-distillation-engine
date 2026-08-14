from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import combinations
from typing import Iterable

from .evidence import EvidenceCatalog, EvidenceGate
from .gates import (
    ProceduralActionGate,
    SemanticSkillGate,
    SkillExecutabilityGate,
    SkillStructureGate,
)
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
        self.procedural = ProceduralActionGate()
        self.semantic_skill = SemanticSkillGate()

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
        report.issues.extend(self.procedural.validate(candidate, catalog).issues)
        report.issues.extend(self.semantic_skill.validate(cluster, candidate, catalog).issues)
        concept_phrases = {
            normalize_excerpt(str(item.get("title", ""))).strip()
            for item in cluster.cohesion_assessment.get("concepts", [])
            if isinstance(item, dict) and str(item.get("title", "")).strip()
        }
        for index, action in enumerate(candidate.actions, start=1):
            evidence_text = " ".join(
                item.excerpt for evidence_id in action.evidence_ids
                if (item := catalog.get(evidence_id)) is not None
            )
            phrases = [
                phrase.strip()
                for quoted in re.findall(r"[“\"]([^”\"]+)[”\"]", action.text)
                for phrase in quoted.split("、")
                if phrase.strip()
            ]
            for phrase in phrases:
                if phrase in {"断风", "势变"}:
                    report.add(
                        "error", "arbitrary-chinese-bigram",
                        f"Meaningless sliding-window phrase is prohibited: {phrase}",
                        f"actions:{index}",
                    )
                if phrase not in evidence_text and phrase not in concept_phrases:
                    report.add(
                        "error", "semantic-signal-not-grounded",
                        f"Action signal is absent from its Evidence and Method concepts: {phrase}",
                        f"actions:{index}",
                    )
        for left_index, left in enumerate(candidate.actions):
            for right_index, right in enumerate(candidate.actions[left_index + 1:], start=left_index + 1):
                left_shape = re.sub(r"[“\"][^”\"]+[”\"]", "<object>", normalize_excerpt(left.text))
                right_shape = re.sub(r"[“\"][^”\"]+[”\"]", "<object>", normalize_excerpt(right.text))
                if SequenceMatcher(None, left_shape, right_shape).ratio() >= 0.85:
                    report.add(
                        "error", "procedural-action-template-duplicate",
                        "Skill actions repeat the same procedural role with substituted words.",
                        f"actions:{left_index + 1},{right_index + 1}",
                    )
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

    VERSION = "skill-synthesizer-v2-procedural"
    CLAUSE_SPLIT = re.compile(r"[。！？!?；;]+")
    CONDITION_SIGNAL = re.compile(r"(?:当|若|如果|一旦|之前|之后|时|没有|缺少|无法|不能|需要|必须|应当|从不)")
    OPERATIONS = (
        (("比较", "对照", "区分", "分辨"), "比较", "输出“相符/冲突/缺失”三态结果"),
        (("核验", "验证", "确认", "检查"), "核验", "输出“已确认/待确认”核验结果"),
        (("记录", "复盘", "回顾", "总结"), "记录", "生成包含条件、行动和结果的记录"),
        (("停止", "退出", "卖出", "止损"), "核对", "记录退出条件及其满足状态"),
        (("控制", "限制", "约束", "调整"), "检查", "输出约束是否满足的状态"),
        (("观察", "识别", "分析", "判断", "评估"), "识别", "输出可观察信号清单"),
        (("买入", "买进", "卖出", "交易", "执行", "选择"), "检查", "记录执行条件和可观察结果"),
    )
    EVIDENCE_SIGNAL_TERMS = (
        "市场", "价格", "交易", "行情", "趋势", "信号", "风险", "损失", "亏损", "盈利",
        "头寸", "股票", "买进", "卖出", "观察", "判断", "记录", "研究", "变化", "错误",
        "事实", "经验", "等待", "行动", "结果", "预测", "核实", "控制", "独立", "公众",
        "信息", "原因", "历史", "支持", "反对", "上涨", "下跌", "资金", "分销", "持有人",
        "交易者", "生涯", "经历", "技巧", "学习", "认识", "自我", "重复", "教训", "心理",
        "观点", "能力", "注意力", "潜意识", "元认知", "成长", "反馈", "耐心", "焦虑", "困难",
        "认知", "思考", "情绪", "大脑", "专注", "选择", "时间", "阅读", "练习", "目标",
        "环境", "反思", "改变", "知识", "身体", "习惯", "觉知", "意识", "理性", "感性",
        "暴露", "退出", "条件", "停止", "增加", "止损", "纪律", "限制", "扩大", "范围",
        "比较", "核验", "区分", "计划", "偏差", "修正", "执行", "连续", "波动", "实际",
        "assess", "analyze", "overall", "market", "trend", "trade", "observe", "facts",
        "alignment", "price", "signals", "compare", "prevailing", "risk", "loss", "discipline",
        "position", "exit", "record", "feedback", "condition", "result", "plan", "error",
    )
    SIGNAL_STOP = {
        "一个", "这个", "那个", "时候", "自己", "可以", "如果", "因为", "所以", "但是",
        "已经", "还是", "就是", "不是", "没有", "他们", "我们", "你们", "什么", "怎么",
    }
    ACTION_ROLES = (
        ("识别", "当判断对象首次进入流程时", "输出可观察原文信号清单", "无法定位任一信号则停止并转人工核验"),
        ("比较", "当原文信号清单已经形成时", "输出支持、冲突或缺失三态对照", "出现无法解释的冲突则停止并升级复核"),
        ("核验", "当三态对照存在关键条件或缺失时", "输出条件已满足、未满足或待确认状态", "关键条件缺失则停止后续动作"),
        ("记录", "当前述信号与条件均完成核验时", "生成包含信号、条件和结果的复核记录", "记录无法绑定原文定位时拒绝提交"),
    )

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
            concept_anchors = [
                normalize_excerpt(str(item.get("title", ""))).strip()
                for item in cluster.cohesion_assessment.get("concepts", [])
                if isinstance(item, dict) and str(item.get("title", "")).strip()
            ]
            if not concept_anchors:
                concept_anchors = [cluster.title]
            action_values: list[GroundedStatement] = []
            insufficient: list[str] = []
            for index, item in enumerate(evidence, start=1):
                anchor = concept_anchors[(index - 1) % len(concept_anchors)]
                signals = self._evidence_signal_terms(item.excerpt, anchor)
                if len(signals) < 3:
                    insufficient.append(item.evidence_id)
                    continue
                action_values.append(GroundedStatement(
                    self._procedural_action(cluster, anchor, index, signals),
                    (item.evidence_id,), "source",
                ))
            if insufficient or len(action_values) < 2:
                validation = ValidationReport()
                validation.add(
                    "error", "insufficient-semantic-signal",
                    "Skill action synthesis could not locate at least three complete Evidence-bound phrases for every selected action.",
                    ",".join(insufficient),
                )
                return SkillSynthesisOutcome(None, validation)
            actions = tuple(action_values)
            signal_one = concept_anchors[0]
            signal_two = concept_anchors[1] if len(concept_anchors) > 1 else signal_one

            def policy(text: str) -> GroundedStatement:
                return GroundedStatement(text, (), "engine_policy")

            candidate = SkillCandidate(
                title=cluster.title,
                positioning=policy(
                    f"把{cluster.title}用于核对“{signal_one}”与“{signal_two}”所代表的原文信号，并形成可停止、可复核的判断流程。"
                ),
                scenarios=(policy(
                    f"当任务需要依据“{signal_one}”或“{signal_two}”执行{cluster.title}，且能够访问绑定 Evidence 时使用。"
                ),),
                input_requirements=(
                    policy(f"提供{cluster.title}的判断对象，以及可核对“{signal_one}”的当前事实。"),
                    policy(f"提供可核对“{signal_two}”的约束、预期信号与实际结果；缺失项必须明确标记。"),
                ),
                decision_flow=(
                    policy(f"先确认请求是否属于{cluster.title}，并列出与“{signal_one}”相关的可观察事实。"),
                    policy(f"再比较“{signal_one}”与“{signal_two}”对应的 Evidence 信号，标记相符、冲突和缺失。"),
                    policy(f"若{cluster.title}的关键 Evidence 冲突或缺失，停止执行并转人工复核。"),
                ),
                actions=actions,
                output_format=policy(
                    f"输出《{cluster.title}检查记录》，包含输入事实、逐步状态、Evidence 定位、停止原因和待复核项。"
                ),
                boundaries=(
                    policy(f"{cluster.title}只解释绑定书籍、conversion 与 Evidence 中可核验的信号，不外推为普遍规律。"),
                    policy(f"{cluster.title}在明确人工复核前始终保持 candidate 状态。"),
                ),
                prohibited_scenarios=(
                    policy(f"当{cluster.title}缺少至少三条合法 Evidence 或 Evidence 冲突未解决时禁止使用。"),
                    policy(f"禁止对{cluster.title}自动 approve、自动 expose 或自动连接项目。"),
                ),
                provider=f"synthesis:{self.VERSION}",
                confidence=cluster.confidence,
            )
        validation = self.gate.validate(cluster, candidate, catalog)
        return SkillSynthesisOutcome(candidate if validation.passed else None, validation)

    @classmethod
    def _procedural_action(cls, cluster: MethodCluster, anchor: str, index: int, signals: tuple[str, ...]) -> str:
        operation, condition, output, stop = cls.ACTION_ROLES[(index - 1) % len(cls.ACTION_ROLES)]
        object_text = normalize_excerpt(anchor).strip() or normalize_excerpt(cluster.title).strip()
        evidence_signals = "、".join(signals)
        return (
            f"{condition}，{operation}“{object_text}”对应的原文信号“{evidence_signals}”，"
            f"{output}；{stop}。"
        )

    @classmethod
    def _evidence_signal_terms(cls, excerpt: str, anchor: str) -> tuple[str, ...]:
        normalized = normalize_excerpt(excerpt)
        candidates: list[tuple[int, str]] = []
        for term in cls.EVIDENCE_SIGNAL_TERMS:
            position = normalized.find(term)
            if position >= 0:
                candidates.append((position, term))
        normalized_anchor = normalize_excerpt(anchor).strip(" ：:，,。.；;、")
        if len(normalized_anchor) >= 2:
            position = normalized.find(normalized_anchor)
            if position >= 0 and normalized_anchor not in cls.SIGNAL_STOP:
                candidates.append((position, normalized_anchor))
        selected: list[str] = []
        for _position, term in sorted(candidates, key=lambda item: (item[0], -len(item[1]), item[1])):
            if term not in selected and not any(term in prior or prior in term for prior in selected):
                selected.append(term)
            if len(selected) >= 4:
                break
        return tuple(selected[:4])

    @classmethod
    def _signal_phrase(cls, excerpt: str, limit: int) -> str:
        clauses = [cls._clean_clause(item) for item in cls.CLAUSE_SPLIT.split(normalize_excerpt(excerpt))]
        clauses = [item for item in clauses if len(item) >= 4]
        if not clauses:
            return cls._clip(cls._clean_clause(excerpt), limit)
        def score(value: str) -> tuple[int, int]:
            action_hits = sum(value.count(keyword) for group, _verb, _output in cls.OPERATIONS for keyword in group)
            condition_hits = len(cls.CONDITION_SIGNAL.findall(value))
            return action_hits * 3 + condition_hits * 2, -len(value)
        selected = max(clauses, key=score)
        return cls._clip(selected, limit)

    @staticmethod
    def _clean_clause(value: str) -> str:
        value = normalize_excerpt(value).strip(" \t\r\n\"'“”‘’《》")
        value = re.sub(r"^(?:他说|她说|我说|他问|她问|我问)[：:,，]?", "", value)
        return value.strip()

    @staticmethod
    def _clip(value: str, limit: int) -> str:
        value = value.strip()
        if len(value) <= limit:
            return value
        clipped = value[:limit]
        boundary = max(clipped.rfind("，"), clipped.rfind(","), clipped.rfind("："), clipped.rfind(":"))
        return (clipped[:boundary] if boundary >= max(8, limit // 2) else clipped).rstrip(" ，,：:")


class KnowledgeSynthesizer:
    """Cluster stable concepts using semantic, Evidence, chapter, purpose, and action signals."""

    VERSION = "knowledge-synthesizer-v1"
    MAX_CLUSTERS = 8
    MAX_CONCEPTS_PER_CLUSTER = 6
    PURPOSES = (
        ("assessment", "分析判断", ("判断", "评估", "分析", "识别", "观察", "比较", "验证", "核验", "趋势", "反馈", "信号", "概率", "可能性", "assess", "analysis", "judgment", "observe", "trend", "signal", "fact", "test")),
        ("risk", "风险控制", ("控制", "风险", "纪律", "约束", "止损", "避免", "禁止", "限制", "安全", "损失", "亏损", "危险", "破产", "保护", "下跌", "risk", "loss", "discipline", "limit", "protect", "conflict", "dysfunction")),
        ("review", "复盘改进", ("复盘", "回顾", "记录", "总结", "反馈", "修正", "改进", "检查", "经验", "教训", "思考", "历史", "研究", "学习", "review", "record", "learn", "lesson", "feedback", "improve")),
        ("planning", "计划准备", ("计划", "准备", "策略", "规则", "目标", "条件", "时机", "等待", "耐心", "预期", "机会", "前提", "plan", "prepare", "strategy", "rule", "condition", "patience", "opportunity", "capacity")),
        ("execution", "执行行动", ("执行", "行动", "操作", "交易", "买入", "买进", "卖出", "调整", "退出", "进入", "持有", "execute", "execution", "trade", "trading", "buy", "sell", "exit", "hold", "position")),
        ("decision", "决策选择", ("选择", "决策", "取舍", "确认", "拒绝", "接受", "决定", "决断", "进场", "离场", "decide", "decision", "select", "confirm", "reject", "accept", "alignment")),
    )
    PURPOSE_FAMILIES = (
        ("decision-cycle", "决策分析", ("assessment", "planning", "decision")),
        ("learning-cycle", "复盘规划", ("review", "planning")),
        ("risk-action", "风险执行", ("risk", "execution")),
    )
    ACTIONS = re.compile(r"(?:判断|评估|分析|识别|观察|比较|验证|控制|约束|停止|等待|记录|复盘|检查|选择|执行|调整|买入|买进|卖出|退出|进入|持有|思考|总结|研究|学习|预期|保护|assess|analy[sz]e|identify|observe|compare|verify|control|limit|stop|wait|record|review|check|select|execute|adjust|buy|sell|exit|enter|hold|protect)", re.IGNORECASE)
    SEMANTIC_STOP = {
        "判断", "评估", "分析", "识别", "观察", "比较", "验证", "控制", "约束", "停止", "等待",
        "记录", "复盘", "检查", "选择", "执行", "调整", "买入", "买进", "卖出", "退出", "进入",
        "持有", "思考", "总结", "研究", "学习", "预期", "保护", "方法", "流程", "行动", "决策",
        "输出", "保存", "结果", "并",
    }
    DOMAIN_TOKENS = {
        "风险", "纪律", "仓位", "损失", "暴露", "退出", "价格", "趋势", "市场", "信号", "事实",
        "交易", "计划", "错误", "反馈", "偏差", "条件", "修正", "判断", "连续", "波动",
        "预期", "实际", "行动", "重复",
        "risk", "discipline", "position", "loss", "exit", "price", "trend", "market", "signal", "fact",
        "trade", "trading", "plan", "error", "feedback", "condition", "judgment", "liquidity", "execution",
    }
    FRAGMENT_PREFIX = re.compile(r"^(?:我|你|他|她|它|我们|你们|他们|这|那|无论|如果|因为|所以|但是|而且|虽然|当时|其实|当然|也许|报告显示|公众应当|公众应该|读者应当)")
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
            # A large chapter/purpose bucket can contain unrelated concepts.  Build
            # bounded pair cohorts first so unrelated members cannot dilute an
            # otherwise auditable Method, and let _cluster enforce the full
            # multi-signal threshold on each cohort.
            groups: list[list[_ConceptProfile]] = []
            ranked_profiles = sorted(purpose_profiles, key=self._profile_rank)
            for left, right in combinations(ranked_profiles, 2):
                if self._pair_can_be_cohesive(left, right):
                    groups.append([left, right])
            # Some valid pairs bind only one Evidence each.  A bounded triple
            # cohort lets the existing three-Evidence gate decide without ever
            # weakening it.
            for triple in combinations(ranked_profiles[:24], 3):
                edges = sum(
                    self._pair_can_be_cohesive(left, right)
                    for left, right in combinations(triple, 2)
                )
                evidence_ids = {
                    evidence_id
                    for profile in triple
                    for evidence_id in profile.concept.evidence_ids
                }
                if edges >= 2 and len(evidence_ids) >= 3:
                    groups.append(list(triple))
            if not groups:
                chapter_groups = [items for items in by_chapter.values() if len(items) >= 2]
                groups = chapter_groups or [purpose_profiles]
            groups = sorted(groups, key=self._group_rank)[:64]
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
                len(members & set(prior.concept_ids))
                / min(len(members), len(set(prior.concept_ids))) >= 0.75
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

    @classmethod
    def _pair_can_be_cohesive(cls, left: _ConceptProfile, right: _ConceptProfile) -> bool:
        lexical = cls._jaccard(left.tokens, right.tokens)
        shared_domain = bool(cls._domain_terms(left) & cls._domain_terms(right))
        shared_chapter = bool(left.sections & right.sections)
        action_flow = cls._jaccard(left.actions, right.actions)
        semantic = lexical >= 0.012 or shared_domain
        secondary = shared_chapter or action_flow >= 0.08
        return semantic and secondary

    @classmethod
    def _group_rank(cls, group: list[_ConceptProfile]) -> tuple[float, float, tuple[int, float, int, str]]:
        if len(group) < 2:
            return (0.0, 0.0, cls._profile_rank(group[0]))
        left, right = group[0], group[1]
        lexical = cls._jaccard(left.tokens, right.tokens)
        action = cls._jaccard(left.actions, right.actions)
        return (-lexical, -action, cls._profile_rank(left))

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
            tokens=frozenset(self._semantic_tokens(text)),
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
        lexical_semantic = sum(self._jaccard(left.tokens, right.tokens) for left, right in pairs) / max(len(pairs), 1)
        purpose_alignment = sum(
            1.0 if left.purpose == right.purpose else 0.65
            for left, right in pairs
        ) / max(len(pairs), 1)
        semantic = min(1.0, lexical_semantic * 4.0 + purpose_alignment * 0.35)
        domain_sets = [self._domain_terms(profile) for profile in profiles]
        shared_domain_pairs = sum(bool(left & right) for left, right in combinations(domain_sets, 2))
        shared_domain_ratio = shared_domain_pairs / max(len(pairs), 1)
        chapter_pairs = sum(bool(left.sections & right.sections) for left, right in pairs)
        chapter_ratio = chapter_pairs / max(len(pairs), 1)
        action_overlap = sum(self._jaccard(left.actions, right.actions) for left, right in pairs) / max(len(pairs), 1)
        thresholds = {"semantic": 0.35, "lexical_floor": 0.012, "shared_domain": 0.66, "chapter": 0.34, "action_flow": 0.08}
        signals = {
            "semantic": semantic >= thresholds["semantic"] and (
                lexical_semantic >= thresholds["lexical_floor"]
                or shared_domain_ratio >= thresholds["shared_domain"]
                or (chapter_ratio >= 0.99 and action_overlap >= 0.12)
            ),
            "chapter": chapter_ratio >= thresholds["chapter"],
            "action_flow": action_overlap >= thresholds["action_flow"],
        }
        strong_signals = sorted(name for name, passed in signals.items() if passed)
        if not signals["semantic"] or len(strong_signals) < 2:
            reasons.append(
                "insufficient multi-signal cohesion: semantic signal and at least one of chapter/action-flow are required"
            )
            return None, reasons
        confidence = min(0.98, (
            0.35
            + 0.20 * semantic
            + 0.20 * chapter_ratio
            + 0.15 * min(1.0, action_overlap * 3)
            + 0.10 * min(1.0, len(evidence_ids) / 5)
        ))
        selected = sorted(profiles, key=self._profile_rank)
        anchors: list[str] = []
        for profile in selected:
            title = normalize_excerpt(profile.concept.canonical_title).strip(" ：:，,。.")
            if title and title not in anchors:
                anchors.append(title)
            if len(anchors) >= 2:
                break
        method_title = f"{anchors[0]}与{anchors[1]}的{purpose_label}方法"
        method_title = method_title[:96].rstrip("与的")
        concept_ids = tuple(sorted(item.concept.canonical_concept_id for item in profiles))
        creation_reason = (
            f"semantic_similarity={semantic:.3f}; lexical_similarity={lexical_semantic:.3f}; "
            f"source_provenance={source_book}; shared_chapter_pairs={chapter_pairs}/{len(pairs)}; decision_purpose={purpose}; "
            f"action_flow_overlap={action_overlap:.3f}; concepts={len(concept_ids)}; evidence={len(evidence_ids)}"
        )
        cohesion_assessment = {
            "schema_version": 1,
            "decision": "passed",
            "goal": f"将 {', '.join(anchors)} 聚合为面向{purpose_label}的可审查判断与行动流程。",
            "scores": {
                "semantic": round(semantic, 4),
                "lexical_semantic": round(lexical_semantic, 4),
                "purpose_alignment": round(purpose_alignment, 4),
                "shared_domain": round(shared_domain_ratio, 4),
                "chapter": round(chapter_ratio, 4),
                "action_flow": round(action_overlap, 4),
            },
            "thresholds": thresholds,
            "strong_signals": strong_signals,
            "concepts": [
                {"id": item.concept.canonical_concept_id, "title": item.concept.canonical_title}
                for item in selected
            ],
            "evidence_ids": evidence_ids,
            "conflicts": [],
            "reason": creation_reason,
        }
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
            cohesion_assessment=cohesion_assessment,
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

    @classmethod
    def _domain_terms(cls, profile: _ConceptProfile) -> set[str]:
        text = normalize_excerpt(
            profile.concept.canonical_title + " " + profile.concept.definition.text
        )
        return {term for term in cls.DOMAIN_TOKENS if term in text}

    @staticmethod
    def _tokens(value: str) -> set[str]:
        lowered = value.casefold()
        tokens = set(re.findall(r"[a-z0-9]{2,}", lowered))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
        tokens.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
        return {item for item in tokens if item}

    @classmethod
    def _semantic_tokens(cls, value: str) -> set[str]:
        cleaned = value
        for token in sorted(cls.SEMANTIC_STOP, key=len, reverse=True):
            cleaned = cleaned.replace(token, " ")
        return cls._tokens(cleaned)

    @staticmethod
    def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
        union = left | right
        return 0.0 if not union else len(left & right) / len(union)
