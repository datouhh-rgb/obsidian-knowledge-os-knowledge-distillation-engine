from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from jsonschema import Draft202012Validator

from .evidence import EvidenceCatalog
from .models import MethodCluster, SkillCandidate, ValidationReport
from .utils import normalize_excerpt


def grounded_statement_schema(*, source_only: bool = False) -> dict[str, Any]:
    origin: dict[str, Any] = {"const": "source"} if source_only else {
        "type": "string", "enum": ["source", "engine_policy"]
    }
    required = ["text", "evidence_ids"] + ([] if source_only else ["origin"])
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 10_000},
            "evidence_ids": {
                "type": "array", "maxItems": 100,
                "items": {"type": "string", "minLength": 1, "maxLength": 100},
            },
            "origin": origin,
        },
        "allOf": [{
            "if": {"properties": {"origin": {"const": "source"}}},
            "then": {"properties": {"evidence_ids": {"minItems": 1}}},
        }],
    }


def skill_candidate_schema(*, include_provider: bool = True, source_only: bool = False) -> dict[str, Any]:
    statement = grounded_statement_schema(source_only=source_only)
    required = [
        "title", "positioning", "scenarios", "input_requirements", "decision_flow",
        "actions", "output_format", "boundaries", "prohibited_scenarios",
    ]
    properties: dict[str, Any] = {
        "title": {"type": "string", "minLength": 2, "maxLength": 100},
        "positioning": statement,
        "scenarios": {"type": "array", "minItems": 1, "maxItems": 100, "items": statement},
        "input_requirements": {"type": "array", "minItems": 1, "maxItems": 100, "items": statement},
        "decision_flow": {"type": "array", "minItems": 2, "maxItems": 100, "items": statement},
        "actions": {"type": "array", "minItems": 2, "maxItems": 100, "items": statement},
        "output_format": statement,
        "boundaries": {"type": "array", "minItems": 1, "maxItems": 100, "items": statement},
        "prohibited_scenarios": {"type": "array", "minItems": 1, "maxItems": 100, "items": statement},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    }
    if include_provider:
        required.extend(["provider", "confidence"])
        properties["provider"] = {"type": "string", "minLength": 1, "maxLength": 200}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


class SkillStructureGate:
    """Fail closed before evidence or semantic interpretation is attempted."""

    def validate(self, candidate: SkillCandidate) -> ValidationReport:
        report = ValidationReport()
        errors = sorted(
            Draft202012Validator(skill_candidate_schema()).iter_errors(candidate.semantic_payload()),
            key=lambda item: list(item.path),
        )
        for error in errors:
            report.add("error", "skill-structure", error.message, error.json_path)
        return report

    def require(self, candidate: SkillCandidate) -> None:
        report = self.validate(candidate)
        if report.errors:
            first = report.errors[0]
            raise ValueError(f"Skill structure gate failed at {first.path}: {first.message}")


class SkillExecutabilityGate:
    """Check that a candidate is an agent-callable procedure, not a summary shell."""

    REQUIRED = {
        "scenarios": 1,
        "input_requirements": 1,
        "decision_flow": 2,
        "actions": 2,
        "boundaries": 1,
        "prohibited_scenarios": 1,
    }

    def score(self, candidate: SkillCandidate) -> float:
        checks = [
            bool(candidate.positioning.text.strip()),
            *(len(getattr(candidate, field)) >= minimum for field, minimum in self.REQUIRED.items()),
            bool(candidate.output_format.text.strip()),
            all(item.text.strip() for item in candidate.decision_flow),
            all(item.text.strip() for item in candidate.actions),
        ]
        return round(100.0 * sum(bool(item) for item in checks) / len(checks), 2)

    def validate(self, candidate: SkillCandidate) -> ValidationReport:
        report = ValidationReport()
        score = self.score(candidate)
        if score < 100:
            report.add("error", "skill-not-executable", f"Executability is {score}; all mandatory procedural fields are required.")
        if candidate.positioning.text.strip() == candidate.output_format.text.strip():
            report.add("error", "skill-summary-shell", "Positioning and output format cannot be the same summary statement.")
        if len({item.text.casefold().strip() for item in candidate.actions}) < 2:
            report.add("error", "skill-actions-not-distinct", "At least two distinct executable actions are required.")
        return report

    def require(self, candidate: SkillCandidate) -> None:
        report = self.validate(candidate)
        if report.errors:
            first = report.errors[0]
            raise ValueError(f"Skill executability gate failed: {first.message}")


class ProceduralActionGate:
    """Reject summaries and quotations that only look like procedural actions."""

    VERSION = "procedural-action-gate-v1"
    VERB = re.compile(
        r"(?:检查|核对|核验|验证|确认|比较|记录|列出|识别|观察|分析|评估|计算|"
        r"标记|选择|执行|调整|停止|终止|拒绝|转交|保存|输出|生成|回滚|等待|"
        r"check|verify|confirm|compare|record|list|identify|observe|analy[sz]e|"
        r"evaluate|calculate|mark|select|execute|adjust|stop|block|escalate|save|output|return)",
        re.IGNORECASE,
    )
    CONDITION = re.compile(
        r"(?:当|若|如果|一旦|在.{0,36}时|之前|之后|条件|前提|"
        r"when|if|unless|before|after|condition|provided that)",
        re.IGNORECASE,
    )
    OBSERVABLE = re.compile(
        r"(?:输出|生成|记录|保存|标记|清单|结果|状态|结论|报告|日志|停止|终止|拒绝|转人工|待复核|"
        r"output|return|record|save|mark|list|result|status|report|log|stop|block|escalate)",
        re.IGNORECASE,
    )
    QUOTED_NARRATIVE = re.compile(r"^[\s\"'“”‘’《》]*(?:我|他|她|他们|我们|朋友|太太|先生|有一天|当时)")

    def validate(self, candidate: SkillCandidate, catalog: EvidenceCatalog) -> ValidationReport:
        report = ValidationReport()
        for index, action in enumerate(candidate.actions, start=1):
            path = f"{candidate.title}:actions:{index}"
            text = normalize_excerpt(action.text).strip()
            if action.origin != "source" or not action.evidence_ids:
                report.add(
                    "error", "procedural-action-evidence-binding",
                    "Every executable action must be a statement-level source abstraction bound to Evidence.", path,
                )
                continue
            evidence = [catalog.get(item) for item in action.evidence_ids]
            if any(item is None for item in evidence):
                report.add("error", "procedural-action-unknown-evidence", "Action references unknown Evidence.", path)
                continue
            excerpts = [normalize_excerpt(item.excerpt).strip() for item in evidence if item is not None]
            semantic_lengths = [
                len(re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", excerpt.casefold()))
                for excerpt in excerpts
            ]
            if not semantic_lengths or max(semantic_lengths) < 12:
                report.add(
                    "error", "procedural-action-thin-evidence",
                    "Executable action Evidence is too shallow to support a production procedure.", path,
                )
            if any(self._is_raw_excerpt(text, excerpt) for excerpt in excerpts):
                report.add(
                    "error", "procedural-action-raw-excerpt",
                    "A raw Evidence excerpt or near-verbatim narrative cannot be used as an executable action.", path,
                )
            if self.QUOTED_NARRATIVE.search(text) and not (
                self.CONDITION.search(text) and self.OBSERVABLE.search(text)
            ):
                report.add(
                    "error", "procedural-action-narrative",
                    "Narrative prose is not a procedure unless it is converted into a condition, action, and observable result.", path,
                )
            if not self.VERB.search(text):
                report.add("error", "procedural-action-verb", "Action lacks a clear imperative verb.", path)
            if not self.CONDITION.search(text):
                report.add("error", "procedural-action-condition", "Action lacks an explicit execution condition.", path)
            if not self.OBSERVABLE.search(text):
                report.add(
                    "error", "procedural-action-observable",
                    "Action lacks a verifiable output or stop condition.", path,
                )
            if not self._has_object(text):
                report.add("error", "procedural-action-object", "Action lacks a concrete object.", path)
        return report

    @staticmethod
    def _is_raw_excerpt(action: str, excerpt: str) -> bool:
        if not action or not excerpt:
            return False
        stripped_action = action.strip(" \t\r\n\"'“”‘’")
        stripped_excerpt = excerpt.strip(" \t\r\n\"'“”‘’")
        if stripped_action == stripped_excerpt:
            return True
        common = SequenceMatcher(None, stripped_action.casefold(), stripped_excerpt.casefold()).find_longest_match()
        if common.size >= 24 and common.size / max(len(stripped_action), 1) >= 0.25:
            return True
        ratio = SequenceMatcher(None, stripped_action.casefold(), stripped_excerpt.casefold()).ratio()
        return ratio >= 0.90 and len(stripped_action) <= max(40, int(len(stripped_excerpt) * 1.25))

    @classmethod
    def _has_object(cls, text: str) -> bool:
        # A quoted Evidence phrase is a concrete, inspectable operation object.
        # Check it before punctuation trimming because excerpts commonly contain
        # sentence punctuation of their own.
        if re.search(r'["“][^"”]{2,}["”]', text):
            return True
        match = cls.VERB.search(text)
        if match is None:
            return False
        tail = re.sub(r"[；;。.!！?？].*$", "", text[match.end():]).strip(" ：:，,、")
        return len(re.sub(r"[\W_]", "", tail, flags=re.UNICODE)) >= 2


class SemanticSkillGate:
    """Require every generated Skill field to carry Method-specific meaning."""

    VERSION = "semantic-skill-gate-v1"
    GENERIC_TEMPLATES = {
        "apply method cluster purpose and boundaries",
        "use when the task matches the method cluster purpose and the required source facts are available",
        "provide the decision target current constraints and the relevant observable facts",
        "provide or confirm access to the bound source evidence before execution",
        "confirm that the request matches the method cluster purpose and boundaries",
        "compare the evidence bound concept signals and identify agreement conflict and missing inputs",
        "return the decision context evidence observations actions boundaries and unresolved review items",
    }
    STOPWORDS = {
        "method", "cluster", "evidence", "source", "skill", "candidate", "apply", "task", "input",
        "output", "review", "bound", "绑定", "证据", "方法", "候选", "流程", "输出", "输入", "执行",
    }

    def validate(
        self,
        cluster: MethodCluster,
        candidate: SkillCandidate,
        catalog: EvidenceCatalog,
    ) -> ValidationReport:
        report = ValidationReport()
        evidence_text = " ".join(
            item.excerpt for evidence_id in cluster.evidence_ids
            if (item := catalog.get(evidence_id)) is not None
        )
        domain_tokens = (
            self._tokens(cluster.title + " " + cluster.description + " " + evidence_text)
            - self.STOPWORDS
        )
        groups = {
            "positioning": candidate.positioning.text,
            "scenarios": " ".join(item.text for item in candidate.scenarios),
            "input_requirements": " ".join(item.text for item in candidate.input_requirements),
            "decision_flow": " ".join(item.text for item in candidate.decision_flow),
            "actions": " ".join(item.text for item in candidate.actions),
            "output_format": candidate.output_format.text,
            "boundaries": " ".join(item.text for item in candidate.boundaries),
            "prohibited_scenarios": " ".join(item.text for item in candidate.prohibited_scenarios),
        }
        generic_hits = 0
        for field, text in groups.items():
            normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text.casefold()).strip()
            if normalized in self.GENERIC_TEMPLATES:
                generic_hits += 1
                report.add(
                    "error", "semantic-skill-generic-field",
                    f"{field} is a generic engine shell rather than Method-specific guidance.", field,
                )
            if not (self._tokens(text) & domain_tokens):
                report.add(
                    "error", "semantic-skill-field-specificity",
                    f"{field} does not reference any Evidence-bound Method semantic signal.", field,
                )
        if generic_hits >= 2:
            report.add(
                "error", "semantic-skill-generic-shell",
                "A structurally complete generic engine shell is not an executable expert Skill.", candidate.title,
            )
        if not (self._tokens(candidate.title) & domain_tokens):
            report.add("error", "semantic-skill-title", "Skill title is not semantically connected to its Method.", candidate.title)
        return report

    @staticmethod
    def _tokens(value: str) -> set[str]:
        lowered = value.casefold()
        tokens = set(re.findall(r"[a-z0-9]{2,}", lowered))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
        tokens.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
        return {item for item in tokens if item}
