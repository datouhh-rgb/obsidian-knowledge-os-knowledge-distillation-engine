from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .models import IngestedSource
from .utils import canonical_json, sha256_text


@dataclass(frozen=True)
class ConversionQualityIssue:
    severity: str
    code: str
    message: str
    member: str = ""
    locator: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "member": self.member,
            "locator": self.locator,
        }


@dataclass(frozen=True)
class ConversionQualityAttestation:
    status: str
    gate_version: str
    source_id: str
    conversion_id: str
    normalized_output_sha256: str
    metrics: dict[str, Any]
    issues: tuple[ConversionQualityIssue, ...] = field(default_factory=tuple)
    prior_attestation_difference: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "passed" and not any(item.severity == "blocker" for item in self.issues)

    def bind(self, conversion_id: str) -> "ConversionQualityAttestation":
        return replace(self, conversion_id=conversion_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "gate_version": self.gate_version,
            "source_id": self.source_id,
            "conversion_id": self.conversion_id,
            "normalized_output_sha256": self.normalized_output_sha256,
            "metrics": self.metrics,
            "issues": [item.as_dict() for item in self.issues],
            "prior_attestation_difference": self.prior_attestation_difference,
            "attestation_sha256": sha256_text(canonical_json({
                "status": self.status,
                "gate_version": self.gate_version,
                "source_id": self.source_id,
                "conversion_id": self.conversion_id,
                "normalized_output_sha256": self.normalized_output_sha256,
                "metrics": self.metrics,
                "issues": [item.as_dict() for item in self.issues],
                "prior_attestation_difference": self.prior_attestation_difference,
            })),
        }


class ConversionQualityGate:
    """Fail closed on damaged canonical conversions before chunking or any LLM call."""

    VERSION = "conversion-quality-gate-v1"
    MOJIBAKE = (
        re.compile(r"(?:锟斤拷|鐨勫|涓€|銆[€傛]|鈥[滄]|鍒嗘瀽|鏃犳硶|瀹炴柦)"),
        re.compile(r"(?:Ã.|Â.|â€|ï»¿){2,}"),
    )
    METADATA_LEAKAGE = re.compile(
        r"^(?:source[_ -]?(?:path|sha256|id)|converter(?:_version|_commit)?|kindleunpack(?:_version|_commit)?|"
        r"command[_ -]?fingerprint|conversion[_ -]?(?:id|schema|scope)|unpacked[_ -]?container[_ -]?hash)\s*[:=]",
        re.IGNORECASE | re.MULTILINE,
    )

    @staticmethod
    def schema() -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version", "status", "gate_version", "source_id", "conversion_id",
                "normalized_output_sha256", "metrics", "issues", "prior_attestation_difference",
                "attestation_sha256",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "status": {"enum": ["passed", "blocked"]},
                "gate_version": {"type": "string", "minLength": 1},
                "source_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "conversion_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "normalized_output_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "metrics": {"type": "object", "additionalProperties": True},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["severity", "code", "message", "member", "locator"],
                        "properties": {
                            "severity": {"enum": ["warning", "blocker"]},
                            "code": {"type": "string", "minLength": 1},
                            "message": {"type": "string", "minLength": 1},
                            "member": {"type": "string"},
                            "locator": {"type": "string"},
                        },
                    },
                },
                "prior_attestation_difference": {"type": "object", "additionalProperties": True},
                "attestation_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
        }

    def validate(
        self,
        source: IngestedSource,
        *,
        prior_attestation: dict[str, Any] | None = None,
    ) -> ConversionQualityAttestation:
        text = source.text
        total = max(len(text), 1)
        issues: list[ConversionQualityIssue] = []
        replacement_count = text.count("\ufffd")
        if replacement_count:
            issues.append(ConversionQualityIssue(
                "blocker", "replacement-character",
                f"Canonical conversion contains {replacement_count} U+FFFD replacement character(s).",
            ))
        mojibake_hits: list[str] = []
        for pattern in self.MOJIBAKE:
            mojibake_hits.extend(match.group(0) for match in pattern.finditer(text))
        if mojibake_hits:
            issues.append(ConversionQualityIssue(
                "blocker", "mojibake-pattern",
                f"Detected {len(mojibake_hits)} known mojibake sequence(s): {mojibake_hits[:5]}",
            ))
        control_count = sum(ord(char) < 32 and char not in "\t\r\n" for char in text)
        control_ratio = control_count / total
        if control_count and control_ratio > 0.0001:
            issues.append(ConversionQualityIssue(
                "blocker", "control-character-ratio",
                f"Control character ratio {control_ratio:.6f} exceeds 0.000100.",
            ))
        latin1_noise = len(re.findall(r"[\u00c0-\u00ff]", text))
        cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
        latin_count = len(re.findall(r"[A-Za-z]", text))
        semantic_count = max(cjk_count + latin_count, 1)
        latin1_noise_ratio = latin1_noise / semantic_count
        if latin1_noise >= 20 and latin1_noise_ratio > 0.01:
            issues.append(ConversionQualityIssue(
                "blocker", "cjk-latin-distribution",
                f"Latin-1 noise ratio {latin1_noise_ratio:.6f} is inconsistent with reliable Chinese text.",
            ))
        headings = re.findall(r"(?m)^#{1,6}\s+\S", text)
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if len(item.strip()) >= 20]
        unique_paragraphs = set(paragraphs)
        duplicate_ratio = 0.0 if not paragraphs else 1.0 - len(unique_paragraphs) / len(paragraphs)
        metadata = source.converter_metadata
        config = metadata.get("config", {}) if isinstance(metadata.get("config"), dict) else {}
        spine = config.get("spine_members", []) if isinstance(config, dict) else []
        empty_spine = config.get("empty_spine_members", []) if isinstance(config, dict) else []
        omissions = config.get("image_omissions", []) if isinstance(config, dict) else []
        missing = config.get("missing_resources", []) if isinstance(config, dict) else []
        spine_count = len(spine) if isinstance(spine, list) else 0
        empty_count = len(empty_spine) if isinstance(empty_spine, list) else 0
        empty_ratio = empty_count / max(spine_count, 1)
        if source.source_kind in {"azw3", "epub", "pdf"}:
            if len(text.strip()) < 1_000:
                issues.append(ConversionQualityIssue(
                    "blocker", "implausible-body-length",
                    f"Converted {source.source_kind.upper()} body has only {len(text.strip())} characters.",
                ))
            if not headings and source.source_kind in {"azw3", "epub"}:
                issues.append(ConversionQualityIssue(
                    "blocker", "heading-coverage",
                    "Canonical ebook conversion contains no structural headings.",
                ))
            if spine_count and empty_ratio > 0.25:
                issues.append(ConversionQualityIssue(
                    "blocker", "empty-spine-ratio",
                    f"Empty spine ratio {empty_ratio:.4f} exceeds 0.25.",
                ))
        if duplicate_ratio > 0.35 and len(paragraphs) >= 20:
            issues.append(ConversionQualityIssue(
                "blocker", "duplicate-paragraph-ratio",
                f"Duplicate paragraph ratio {duplicate_ratio:.4f} exceeds 0.35.",
            ))
        if self.METADATA_LEAKAGE.search(text):
            issues.append(ConversionQualityIssue(
                "blocker", "metadata-leakage",
                "Converter/path/hash provenance was found in canonical book text.",
            ))
        if isinstance(missing, list) and missing:
            issues.append(ConversionQualityIssue(
                "blocker", "missing-resources",
                f"Canonical conversion reports {len(missing)} missing resource(s).",
            ))
        if isinstance(omissions, list) and omissions:
            issues.append(ConversionQualityIssue(
                "warning", "image-omissions",
                f"Canonical text declares {len(omissions)} omitted image reference(s).",
            ))
        converter = str(metadata.get("converter", ""))
        if source.source_kind == "azw3" and converter != "KindleUnpack canonical EPUB spine adapter":
            issues.append(ConversionQualityIssue(
                "blocker", "noncanonical-azw3-converter",
                f"AZW3 converter is {converter!r}, not the canonical KindleUnpack adapter.",
            ))
        prior_difference: dict[str, Any] = {}
        if prior_attestation:
            prior_difference = {
                "prior_output_sha256": prior_attestation.get("normalized_output_sha256"),
                "current_output_sha256": sha256_text(text),
                "changed": prior_attestation.get("normalized_output_sha256") != sha256_text(text),
            }
        metrics = {
            "characters": len(text),
            "replacement_character_count": replacement_count,
            "replacement_character_ratio": replacement_count / total,
            "mojibake_hit_count": len(mojibake_hits),
            "control_character_count": control_count,
            "control_character_ratio": control_ratio,
            "cjk_character_count": cjk_count,
            "latin_character_count": latin_count,
            "latin1_noise_count": latin1_noise,
            "latin1_noise_ratio": latin1_noise_ratio,
            "heading_count": len(headings),
            "paragraph_count": len(paragraphs),
            "duplicate_paragraph_ratio": duplicate_ratio,
            "spine_member_count": spine_count,
            "empty_spine_member_count": empty_count,
            "empty_spine_ratio": empty_ratio,
            "missing_resource_count": len(missing) if isinstance(missing, list) else 0,
            "image_omission_count": len(omissions) if isinstance(omissions, list) else 0,
            "llm_calls": 0,
        }
        blocked = any(item.severity == "blocker" for item in issues)
        return ConversionQualityAttestation(
            status="blocked" if blocked else "passed",
            gate_version=self.VERSION,
            source_id=source.source_id,
            conversion_id="",
            normalized_output_sha256=sha256_text(text),
            metrics=metrics,
            issues=tuple(issues),
            prior_attestation_difference=prior_difference,
        )

    @staticmethod
    def load(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None
