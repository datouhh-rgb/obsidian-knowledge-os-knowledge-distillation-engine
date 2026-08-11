from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from .models import SkillCandidate, ValidationReport


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
