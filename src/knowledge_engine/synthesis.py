from __future__ import annotations

import json
import re
import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .evidence import EvidenceCatalog, EvidenceUniverse
from .graph import GRAPH_JSON, GRAPH_MD, ConceptGraph
from .gates import SkillExecutabilityGate, SkillStructureGate, skill_candidate_schema
from .ledger import ASSET_LEDGER_JSON, AssetLedger
from .models import QualityScore, SkillCandidate
from .quality import SkillQualityEvaluator
from .registry import REGISTRY_JSON, REGISTRY_MD, SkillRegistry
from .renderer import ArtifactRenderer
from .semantic import SemanticSupportGate
from .transaction import VaultTransaction
from .utils import canonical_json, now_iso, sha256_file, sha256_text
from .validator import SkillValidator


PROPOSALS_JSON = Path("05_Knowledge/Skills/SYNTHESIS_PROPOSALS.json")
PROPOSALS_DIR = Path("05_Knowledge/Skills/_proposals")


@dataclass
class SynthesisRegistry:
    data: dict[str, Any]

    @classmethod
    def empty(cls) -> "SynthesisRegistry":
        return cls({"schema_version": 3, "engine_version": "3.3.0", "updated_at": None, "proposals": {}})

    @classmethod
    def load(cls, vault: Path) -> "SynthesisRegistry":
        path = vault / PROPOSALS_JSON
        if not path.is_file():
            return cls.empty()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Synthesis Registry 无法读取：{exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("proposals"), dict):
            raise ValueError("Synthesis Registry 结构无效。")
        return cls(data)

    @property
    def proposals(self) -> dict[str, dict[str, Any]]:
        return self.data["proposals"]

    def propose_for_registry(self, registry: SkillRegistry) -> list[dict[str, Any]]:
        members: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
        for record in registry.records.values():
            for version, version_record in record.get("versions", {}).items():
                if version_record.get("kind") != "source-candidate":
                    continue
                members.append((record, version, version_record))
        created: list[dict[str, Any]] = []
        for left_index, left in enumerate(members):
            for right in members[left_index + 1:]:
                pair_members = [left, right]
                source_pairs = {
                    (source.get("source_id"), source.get("conversion_id"))
                    for _, _, version_record in pair_members for source in version_record.get("sources", [])
                }
                if len(source_pairs) < 2:
                    continue
                match = self._match_scores(left, right)
                if match["maximum_score"] < match["threshold"]:
                    continue
                member_keys = sorted(f"{record['skill_id']}@{version}" for record, version, _ in pair_members)
                proposal_id = "synth-" + sha256_text(canonical_json(member_keys))[:24]
                if proposal_id in self.proposals:
                    continue
                title_key = self._normalize_title(str(left[0]["title"]))
                proposal = self._build_proposal(proposal_id, title_key, pair_members)
                proposal["match"] = match
                self.proposals[proposal_id] = proposal
                created.append(proposal)
        if created:
            self.data["updated_at"] = now_iso()
        return created

    @classmethod
    def _match_scores(
        cls,
        left: tuple[dict[str, Any], str, dict[str, Any]],
        right: tuple[dict[str, Any], str, dict[str, Any]],
    ) -> dict[str, Any]:
        left_record, _left_version, left_version_record = left
        right_record, _right_version, right_version_record = right
        left_titles = [str(left_record["title"]), *[str(item) for item in left_record.get("aliases", [])]]
        right_titles = [str(right_record["title"]), *[str(item) for item in right_record.get("aliases", [])]]
        title_score = max(
            100.0 * difflib.SequenceMatcher(None, cls._normalize_title(a), cls._normalize_title(b)).ratio()
            for a in left_titles for b in right_titles
        )
        left_candidate = SkillCandidate.from_payload(left_version_record["content"])
        right_candidate = SkillCandidate.from_payload(right_version_record["content"])
        semantic_score = cls._token_overlap(left_candidate.semantic_text(), right_candidate.semantic_text())
        action_score = cls._token_overlap(
            " ".join(item.text for item in left_candidate.actions),
            " ".join(item.text for item in right_candidate.actions),
        )
        maximum = max(title_score, semantic_score, action_score)
        reasons: list[str] = []
        if title_score >= 85: reasons.append("title-or-alias-similarity")
        if semantic_score >= 85: reasons.append("semantic-overlap")
        if action_score >= 85: reasons.append("action-overlap")
        return {
            "title_score": round(title_score, 2),
            "semantic_score": round(semantic_score, 2),
            "action_score": round(action_score, 2),
            "maximum_score": round(maximum, 2),
            "threshold": 85.0,
            "reasons": reasons,
        }

    @staticmethod
    def _token_overlap(left: str, right: str) -> float:
        def tokens(value: str) -> set[str]:
            lowered = value.casefold()
            result = set(re.findall(r"[a-z0-9]{2,}", lowered))
            chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
            result.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
            return result
        left_tokens, right_tokens = tokens(left), tokens(right)
        return 0.0 if not left_tokens or not right_tokens else 100.0 * len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def _legacy_exact_grouping(self, registry: SkillRegistry) -> list[dict[str, Any]]:
        """Reserved migration hook; V3.1 uses the scored pairwise matcher above."""
        groups: dict[str, list[tuple[dict[str, Any], str, dict[str, Any]]]] = {}
        for record in registry.records.values():
            for version, version_record in record.get("versions", {}).items():
                if version_record.get("kind") == "source-candidate":
                    groups.setdefault(self._normalize_title(str(record["title"])), []).append((record, version, version_record))
        for title_key, group_members in groups.items():
            source_pairs = {
                (source.get("source_id"), source.get("conversion_id"))
                for _, _, version_record in group_members for source in version_record.get("sources", [])
            }
            if len(group_members) < 2 or len(source_pairs) < 2:
                continue
        return []

    def require(self, proposal_id: str) -> dict[str, Any]:
        if proposal_id not in self.proposals:
            raise KeyError(f"Synthesis Proposal 不存在：{proposal_id}")
        return self.proposals[proposal_id]

    def approve(self, proposal_id: str, approver: str, reason: str, candidate: SkillCandidate) -> dict[str, Any]:
        proposal = self.require(proposal_id)
        if proposal.get("status") != "pending":
            raise ValueError("只有 pending proposal 可以批准。")
        available = set(proposal["evidence_ids"])
        bound = {item for statement in candidate.source_statements() for item in statement.evidence_ids}
        if not bound or not bound.issubset(available):
            raise ValueError("综合候选引用了 proposal 之外的 Evidence。")
        evidence_to_pair = proposal["evidence_source_map"]
        pairs = {tuple(evidence_to_pair[item]) for item in bound}
        if len(pairs) < 2:
            raise ValueError("综合候选必须引用至少两个 source/conversion。")
        decision = {
            "decision_id": "synth-approval-" + sha256_text(f"{proposal_id}\0{approver}\0{now_iso()}")[:20],
            "approver": approver, "reason": reason, "approved_at": now_iso(),
            "candidate": candidate.semantic_payload(),
        }
        proposal["status"] = "human-approved-for-materialization"
        proposal["approval"] = decision
        self.data["updated_at"] = now_iso()
        return decision

    def reject(self, proposal_id: str, reviewer: str, reason: str) -> None:
        proposal = self.require(proposal_id)
        proposal["status"] = "rejected"
        proposal["decision"] = {"reviewer": reviewer, "reason": reason, "rejected_at": now_iso()}

    def to_json(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    @staticmethod
    def proposal_json_path(proposal_id: str) -> Path:
        return PROPOSALS_DIR / f"{proposal_id}.json"

    @staticmethod
    def proposal_md_path(proposal_id: str) -> Path:
        return PROPOSALS_DIR / f"{proposal_id}.md"

    @staticmethod
    def proposal_markdown(proposal: dict[str, Any]) -> str:
        lines = [
            "---", f'id: "{proposal["proposal_id"]}"', 'type: "synthesis-proposal"',
            f'status: "{proposal["status"]}"', "---", "", f"# Synthesis Proposal: {proposal['display_title']}", "",
            "## 共同原则", "",
        ]
        lines.extend(f"- {item['text']} · Evidence: {', '.join(item['evidence_ids'])}" for item in proposal["common_principles"]) or lines.append("- 尚无可安全自动确认的共同原则；等待语义审查。")
        lines.extend(["", "## 互补步骤", ""])
        lines.extend(f"- {item['text']} · Evidence: {', '.join(item['evidence_ids'])}" for item in proposal["complementary_steps"]) or lines.append("- 无。")
        lines.extend(["", "## 冲突陈述", ""])
        lines.extend(f"- {item['text']} · Evidence: {', '.join(item['evidence_ids'])}" for item in proposal["conflicts"]) or lines.append("- 未自动判定；人工审查仍必需。")
        lines.extend(["", "## 适用范围差异", ""])
        lines.extend(f"- {item}" for item in proposal["scope_differences"])
        lines.extend(["", "## 候选处置", ""])
        lines.extend(f"- `{item['member']}`：{item['decision']}" for item in proposal["candidate_disposition"])
        lines.extend(["", "## 合并风险与置信度", "", f"- 风险：{proposal['merge_risk']}", f"- 置信度：{proposal['confidence']}", "- 未经 Evidence/semantic review 与明确人工批准，不生成综合 Skill version。", ""])
        return "\n".join(lines)

    @classmethod
    def _build_proposal(cls, proposal_id: str, title_key: str, members: list[tuple[dict[str, Any], str, dict[str, Any]]]) -> dict[str, Any]:
        member_records: list[dict[str, Any]] = []
        evidence_map: dict[str, list[str]] = {}
        statement_map: dict[str, list[dict[str, Any]]] = {}
        for record, version, version_record in members:
            sources = version_record.get("sources", [])
            member = {
                "skill_id": record["skill_id"], "name": record["canonical_name"], "version": version,
                "title": record["title"], "sources": sources,
                "content_fingerprint": version_record["content_fingerprint"],
            }
            member_records.append(member)
            pair = [str(sources[0]["source_id"]), str(sources[0]["conversion_id"])]
            for evidence_id in version_record.get("evidence_bindings", []):
                evidence_map[evidence_id] = pair
            candidate = SkillCandidate.from_payload(version_record["content"])
            for statement in candidate.source_statements():
                key = cls._normalize_statement(statement.text)
                statement_map.setdefault(key, []).append({"text": statement.text, "evidence_ids": list(statement.evidence_ids), "member": f"{record['canonical_name']}@{version}"})
        common: list[dict[str, Any]] = []
        complementary: list[dict[str, Any]] = []
        for occurrences in statement_map.values():
            member_count = len({item["member"] for item in occurrences})
            evidence_ids = sorted({eid for item in occurrences for eid in item["evidence_ids"]})
            record = {"text": occurrences[0]["text"], "evidence_ids": evidence_ids}
            if member_count >= 2:
                common.append(record)
            else:
                complementary.append(record)
        conflicts: list[dict[str, Any]] = []
        polarity: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for occurrences in statement_map.values():
            for item in occurrences:
                core, sign = cls._polarity_core(item["text"])
                if core and sign:
                    polarity.setdefault(core, {}).setdefault(sign, []).append(item)
        for core, sides in polarity.items():
            if "positive" in sides and "negative" in sides:
                items = [*sides["positive"], *sides["negative"]]
                conflicts.append({
                    "text": " / ".join(dict.fromkeys(item["text"] for item in items)),
                    "evidence_ids": sorted({eid for item in items for eid in item["evidence_ids"]}),
                    "core": core,
                })
        all_sources = [source for member in member_records for source in member["sources"]]
        return {
            "proposal_id": proposal_id, "status": "pending", "created_at": now_iso(),
            "display_title": members[0][0]["title"], "normalized_title": title_key,
            "members": member_records, "sources": all_sources,
            "common_principles": common, "complementary_steps": complementary,
            "conflicts": conflicts,
            "scope_differences": [f"{item['name']}@{item['version']} 保留其单一来源适用范围。" for item in member_records],
            "candidate_disposition": [{"member": f"{item['name']}@{item['version']}", "decision": "retained-as-source-candidate"} for item in member_records],
            "merge_risk": "high" if not common else "medium",
            "confidence": 0.3 if not common else 0.6,
            "evidence_ids": sorted(evidence_map), "evidence_source_map": evidence_map,
        }

    @staticmethod
    def _normalize_title(value: str) -> str:
        return re.sub(r"\s+", "", value).casefold()

    @staticmethod
    def _normalize_statement(value: str) -> str:
        return re.sub(r"\W+", "", value).casefold()

    @staticmethod
    def _polarity_core(value: str) -> tuple[str, str | None]:
        lowered = value.casefold()
        negative = bool(re.search(r"(?:不得|禁止|不应|不能|must\s+not|never|do\s+not)", lowered))
        positive = bool(re.search(r"(?:必须|应当|应该|must|always|should)", lowered)) and not negative
        core = re.sub(r"(?:不得|禁止|不应|不能|必须|应当|应该|must\s+not|must|never|always|should|do\s+not)", "", lowered)
        core = re.sub(r"\W+", "", core)
        return core, "negative" if negative else "positive" if positive else None


class SynthesisMaterializationService:
    def __init__(self, vault: Path) -> None:
        self.vault = vault.resolve()
        self.renderer = ArtifactRenderer()

    def materialize(self, proposal_id: str, approver: str, reason: str, candidate_payload: dict[str, Any], name: str | None = None) -> dict[str, Any]:
        schema_errors = sorted(
            Draft202012Validator(skill_candidate_schema()).iter_errors(candidate_payload),
            key=lambda item: list(item.path),
        )
        if schema_errors:
            raise ValueError(f"Synthesis candidate JSON Schema failed at {schema_errors[0].json_path}: {schema_errors[0].message}")
        candidate = SkillCandidate.from_payload(candidate_payload)
        SkillStructureGate().require(candidate)
        with VaultTransaction(self.vault, "synthesis-materialize") as tx:
            proposals = SynthesisRegistry.load(self.vault)
            ledger = AssetLedger.load(self.vault)
            proposal = proposals.require(proposal_id)
            decision = proposals.approve(proposal_id, approver, reason, candidate)
            catalogs = self._catalogs_for_proposal(proposal)
            universe = EvidenceUniverse.from_catalogs(catalogs)
            for statement in candidate.all_statements():
                if statement.origin == "source":
                    if not statement.evidence_ids or any(universe.get(item) is None for item in statement.evidence_ids):
                        raise ValueError("Synthesis EvidenceGate rejected an unbound source statement.")
                elif statement.evidence_ids:
                    raise ValueError("Synthesis EvidenceGate rejected Evidence on an engine_policy statement.")
            semantic = SemanticSupportGate().assess(candidate.all_statements(), universe)
            if semantic.status != "passed":
                raise ValueError("综合候选语义支持未全量通过 supported。")
            SkillExecutabilityGate().require(candidate)
            registry = SkillRegistry.load(self.vault)
            member_ids = {item["skill_id"] for item in proposal["members"]}
            comparable = {skill_id: record for skill_id, record in registry.records.items() if skill_id not in member_ids}
            combined = EvidenceCatalog(
                source_title=proposal["display_title"], source_id="multi-source", conversion_id="multi-conversion",
                source_text="", original_path="", items=universe.items,
            )
            score = SkillQualityEvaluator().score(candidate, combined, comparable, semantic.assessments, ())
            sources = proposal["sources"]
            item = registry.create_synthesis_version(
                name or f"apply-synthesized-{proposal['normalized_title']}", proposal["display_title"], candidate,
                sources, score, semantic.assessments, proposal_id,
                {"provider": "human-synthesis", "proposal_id": proposal_id, "approver": approver},
            )
            overview = Path("05_Knowledge/Skills/_proposals") / f"{proposal_id}.md"
            original = Path(str(sources[0].get("original_path", "06_Sources")))
            skill_text = self.renderer.skill_text(item, combined, overview, original)
            evidence_text = self.renderer.skill_evidence_json(item, combined)
            openai_text = self.renderer.openai_yaml(item)
            root = Path("05_Knowledge/Skills") / item.name
            version_root = root / "versions" / item.version
            validation = SkillValidator().validate_skill_text(skill_text, str(version_root / "SKILL.md"))
            if validation.errors:
                raise ValueError("Synthesis SkillValidator failed: " + "; ".join(issue.message for issue in validation.errors))
            version_text = json.dumps({
                "schema_version": 4, "engine_version": "3.3.0", "skill_id": item.skill_id,
                "name": item.name, "version": item.version, "status": item.status,
                "skill_sha256": sha256_text(skill_text), "evidence_sha256": sha256_text(evidence_text),
                "source_id": item.source_id, "conversion_id": item.conversion_id,
                "proposal_id": proposal_id,
            }, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            registry.seal_version(
                item.name, item.version, sha256_text(skill_text), sha256_text(evidence_text),
                version_record_sha256=sha256_text(version_text),
            )
            next_revision = int(tx.state.get("revision", 0)) + 1
            prior_ledger_path = self.vault / ASSET_LEDGER_JSON
            prior_ledger_hash = sha256_file(prior_ledger_path) if prior_ledger_path.is_file() else None
            ledger.add_skill_version(
                skill_id=item.skill_id, name=item.name, version=item.version,
                source_conversions=[str(source["conversion_id"]) for source in sources],
                transaction_revision=next_revision,
                skill_path=version_root / "SKILL.md", skill_content=skill_text,
                evidence_path=version_root / "references/evidence.json", evidence_content=evidence_text,
                version_path=version_root / "VERSION.json", version_content=version_text,
            )
            ledger.data["revision"] = next_revision
            ledger.data["previous_ledger_sha256"] = prior_ledger_hash
            ledger.data["updated_at"] = now_iso()
            graph = ConceptGraph.load(self.vault)
            graph.add_synthesis(item, proposal["members"], sorted({eid for statement in candidate.source_statements() for eid in statement.evidence_ids}), decision["decision_id"])
            proposal["status"] = "materialized-review"
            proposal["materialized_skill"] = {"skill_id": item.skill_id, "name": item.name, "version": item.version}
            for base in (root, version_root):
                tx.stage_text(base / "SKILL.md", skill_text)
                tx.stage_text(base / "agents/openai.yaml", openai_text)
                tx.stage_text(base / "references/evidence.json", evidence_text)
            tx.stage_text(version_root / "VERSION.json", version_text)
            tx.stage_text(REGISTRY_JSON, registry.to_json())
            tx.stage_text(REGISTRY_MD, registry.to_markdown())
            tx.stage_text(GRAPH_JSON, graph.to_json())
            tx.stage_text(GRAPH_MD, graph.to_markdown())
            tx.stage_text(PROPOSALS_JSON, proposals.to_json())
            tx.stage_text(ASSET_LEDGER_JSON, ledger.to_json())
            tx.stage_text(proposals.proposal_json_path(proposal_id), json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            tx.stage_text(proposals.proposal_md_path(proposal_id), proposals.proposal_markdown(proposal))
            committed = tx.commit({"proposal_id": proposal_id, "skill": item.name, "version": item.version})
            return {"status": "materialized-review", "proposal_id": proposal_id, "skill": item.name, "skill_id": item.skill_id, "version": item.version, "event_id": committed.event_id}

    def _catalogs_for_proposal(self, proposal: dict[str, Any]) -> list[EvidenceCatalog]:
        needed = {(str(item["source_id"]), str(item["conversion_id"])) for item in proposal["sources"]}
        catalogs: list[EvidenceCatalog] = []
        for conversion_path in (self.vault / "06_Sources/Books").rglob("CONVERSION.json"):
            metadata = json.loads(conversion_path.read_text(encoding="utf-8"))
            pair = (str(metadata.get("source_id")), str(metadata.get("conversion_id")))
            if pair not in needed:
                continue
            source_text = self.vault / str(metadata["source_text_path"])
            catalogs.append(EvidenceCatalog.load(conversion_path.parent / "EVIDENCE_CATALOG.json", source_text))
        found = {(item.source_id, item.conversion_id) for item in catalogs}
        if found != needed:
            raise ValueError(f"Proposal Evidence conversion 缺失：{sorted(needed - found)}")
        return catalogs
