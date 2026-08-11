from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .evidence import EvidenceCatalog
from .models import Analysis, CanonicalConcept, MethodCluster, ProviderResult, SkillMaterialization, ValidationReport
from .utils import canonical_json, now_iso, sha256_text


GRAPH_JSON = Path("05_Knowledge/Graph/concept-graph.v3.json")
GRAPH_MD = Path("05_Knowledge/Graph/Concept-Graph-V3.md")


@dataclass(frozen=True)
class GraphDryRunResult:
    passed: bool
    graph: "ConceptGraph | None"
    issues: tuple[dict[str, str], ...]
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    deterministic_digest: str


@dataclass
class ConceptGraph:
    data: dict[str, Any]

    @classmethod
    def empty(cls) -> "ConceptGraph":
        return cls({"schema_version": 3, "engine_version": "3.3.0", "revision": 0, "nodes": {}, "edges": {}})

    @classmethod
    def load(cls, vault: Path) -> "ConceptGraph":
        path = vault / GRAPH_JSON
        if not path.is_file():
            return cls.empty()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Concept Graph 无法读取：{exc}") from exc
        errors = sorted(Draft202012Validator(cls.schema()).iter_errors(data), key=lambda item: list(item.path))
        if errors:
            raise ValueError(f"Concept Graph schema 无效：{errors[0].json_path}: {errors[0].message}")
        graph = cls(data)
        governance = graph.validate_governance()
        if governance.errors:
            raise ValueError("Concept Graph 引用无效：" + "; ".join(item.message for item in governance.errors))
        return graph

    @property
    def nodes(self) -> dict[str, dict[str, Any]]:
        return self.data["nodes"]

    @property
    def edges(self) -> dict[str, dict[str, Any]]:
        return self.data["edges"]

    def merge_distillation(
        self,
        title: str,
        source_id: str,
        conversion_id: str,
        analysis: Analysis,
        catalog: EvidenceCatalog,
        provider: ProviderResult,
        skills: list[SkillMaterialization],
        method_clusters: tuple[MethodCluster, ...] = (),
        canonical_concepts: tuple[CanonicalConcept, ...] = (),
        skill_method_ids: dict[str, str] | None = None,
    ) -> None:
        book_id = self._upsert_node("book", source_id, title, {"source_id": source_id, "aliases": [title]})
        concept_ids: dict[str, str] = {}
        for concept in provider.concepts:
            concept_identity = self._normalize_alias(concept.title)
            concept_id = self._upsert_node("concept", concept_identity, concept.title, {"aliases": [concept.title]})
            concept_ids[concept.title.casefold()] = concept_id
            self._upsert_edge(
                book_id, concept_id, "contains_concept", "verified", concept.provider, concept.confidence,
                source_id, conversion_id, list(concept.definition.evidence_ids), approved_by="structural-gate",
            )
        canonical_by_id = {item.canonical_concept_id: item for item in canonical_concepts}
        method_node_ids: dict[str, str] = {}
        for cluster in method_clusters:
            method_id = self._upsert_node("method", cluster.id, cluster.title, {"aliases": [cluster.title]})
            method_node_ids[cluster.id] = method_id
            self._upsert_edge(
                book_id, method_id, "contains_method", "candidate", "knowledge-synthesis", cluster.confidence,
                source_id, conversion_id, list(cluster.evidence_ids), approved_by=None,
            )
            for canonical_id in cluster.concept_ids:
                canonical = canonical_by_id.get(canonical_id)
                if canonical is None:
                    raise ValueError(f"Method Cluster references unknown canonical concept: {canonical_id}")
                concept_id = concept_ids.get(canonical.canonical_title.casefold()) or self._upsert_node(
                    "concept", self._normalize_alias(canonical.canonical_title), canonical.canonical_title,
                    {"aliases": list(canonical.aliases)},
                )
                self._upsert_edge(
                    concept_id, method_id, "supports", "candidate", "knowledge-synthesis", cluster.confidence,
                    source_id, conversion_id, list(canonical.evidence_ids), approved_by=None,
                )
        for method in analysis.methods:
            method_identity = f"{source_id}\0{method.title.casefold()}"
            method_id = self._upsert_node("method", method_identity, method.title, {"aliases": [method.title]})
            evidence_ids = sorted({eid for item in method.evidence for eid in catalog.ids_for_evidence(item)})
            self._upsert_edge(book_id, method_id, "contains_method", "verified", "rule", 1.0, source_id, conversion_id, evidence_ids, approved_by="structural-gate")
        for relation in provider.relations:
            source_node = concept_ids.get(relation.source_concept.casefold()) or self._upsert_node("concept", self._normalize_alias(relation.source_concept), relation.source_concept, {"aliases": [relation.source_concept]})
            target_node = concept_ids.get(relation.target_concept.casefold()) or self._upsert_node("concept", self._normalize_alias(relation.target_concept), relation.target_concept, {"aliases": [relation.target_concept]})
            status = "candidate" if relation.provider.startswith("llm") or provider.provider.startswith("hybrid") else "verified"
            self._upsert_edge(
                source_node, target_node, "concept_relation", status, relation.provider, relation.confidence,
                source_id, conversion_id, list(relation.relation.evidence_ids),
                statement=relation.relation.text, approved_by=None if status == "candidate" else "structural-gate",
            )
        for skill in skills:
            skill_id = self._upsert_node("skill", skill.skill_id, skill.candidate.title, {"skill_id": skill.skill_id, "skill_name": skill.name, "aliases": [skill.candidate.title]})
            evidence_ids = sorted({eid for item in skill.candidate.source_statements() for eid in item.evidence_ids})
            self._upsert_edge(book_id, skill_id, "produces_skill", "candidate", skill.candidate.provider, skill.candidate.confidence, source_id, conversion_id, evidence_ids, version=skill.version)
            method_cluster_id = (skill_method_ids or {}).get(skill.candidate.fingerprint())
            if method_cluster_id:
                method_id = method_node_ids.get(method_cluster_id)
                if method_id is None:
                    raise ValueError(f"Skill references unknown Method Cluster: {method_cluster_id}")
                self._upsert_edge(
                    method_id, skill_id, "generates", "candidate", skill.candidate.provider,
                    skill.candidate.confidence, source_id, conversion_id, evidence_ids,
                    version=skill.version,
                )

    def dry_run_distillation(
        self,
        title: str,
        source_id: str,
        conversion_id: str,
        analysis: Analysis,
        catalog: EvidenceCatalog,
        provider: ProviderResult,
        skills: list[SkillMaterialization] | None = None,
        method_clusters: tuple[MethodCluster, ...] = (),
        canonical_concepts: tuple[CanonicalConcept, ...] = (),
        skill_method_ids: dict[str, str] | None = None,
    ) -> GraphDryRunResult:
        candidate = ConceptGraph(deepcopy(self.data))
        issues: list[dict[str, str]] = []
        try:
            candidate.merge_distillation(
                title, source_id, conversion_id, analysis, catalog, provider, skills or [],
                method_clusters, canonical_concepts, skill_method_ids,
            )
            governance = candidate.validate_governance()
            for issue in governance.issues:
                if issue.severity == "error":
                    issues.append({"code": issue.code, "message": issue.message})
            concept_aliases: dict[str, set[str]] = {}
            for node_id, node in candidate.nodes.items():
                if node.get("type") != "concept":
                    continue
                for alias in node.get("aliases", []):
                    concept_aliases.setdefault(self._normalize_alias(str(alias)), set()).add(node_id)
            for alias, node_ids_for_alias in concept_aliases.items():
                if alias and len(node_ids_for_alias) > 1:
                    issues.append({"code": "duplicate-concept", "message": f"Concept alias {alias!r} maps to multiple nodes: {sorted(node_ids_for_alias)}"})
            for edge_id, edge in candidate.edges.items():
                if edge.get("source_id") != source_id or edge.get("conversion_id") != conversion_id:
                    continue
                for evidence_id in edge.get("evidence_ids", []):
                    evidence = catalog.get(str(evidence_id))
                    if evidence is None:
                        issues.append({"code": "graph-unknown-evidence", "message": f"{edge_id} references unknown Evidence {evidence_id}"})
                    elif evidence.source_id != source_id or evidence.conversion_id != conversion_id:
                        issues.append({"code": "graph-evidence-scope", "message": f"{edge_id} references Evidence outside the active source/conversion"})
        except (KeyError, TypeError, ValueError) as exc:
            issues.append({"code": "graph-identity-conflict", "message": str(exc)})
            candidate = None
        node_ids = tuple(sorted(candidate.nodes)) if candidate else ()
        edge_ids = tuple(sorted(candidate.edges)) if candidate else ()
        digest = sha256_text(canonical_json({"nodes": node_ids, "edges": edge_ids}))
        return GraphDryRunResult(not issues, candidate, tuple(issues), node_ids, edge_ids, digest)

    def link_project(self, skill_id: str, skill_name: str, skill_title: str, version: str, project: str, event_id: str) -> None:
        skill_node = self._upsert_node("skill", skill_id, skill_title, {"skill_id": skill_id, "skill_name": skill_name, "aliases": [skill_title]})
        project_node = self._upsert_node("project", project.casefold(), project, {"aliases": [project]})
        self._upsert_edge(skill_node, project_node, "used_in_project", "verified", "usage-event", 1.0, "", "", [], approved_by=event_id, version=version, nonce=event_id)

    def add_relation(self, source_node: str, target_node: str, kind: str, status: str, provider: str, confidence: float, evidence_ids: list[str], source_id: str = "", conversion_id: str = "", approved_by: str | None = None, version: str | None = None) -> str:
        if kind not in {"alias", "homonym", "similar", "conflicts", "supersedes", "derived_from"}:
            raise ValueError(f"不支持的治理关系：{kind}")
        return self._upsert_edge(source_node, target_node, kind, status, provider, confidence, source_id, conversion_id, evidence_ids, approved_by=approved_by, version=version)

    def add_synthesis(self, skill: SkillMaterialization, members: list[dict[str, Any]], evidence_ids: list[str], approval_id: str) -> None:
        synthesis_node = self._upsert_node("skill", skill.skill_id, skill.candidate.title, {"skill_id": skill.skill_id, "skill_name": skill.name, "aliases": [skill.candidate.title]})
        for member in members:
            member_node = self._upsert_node("skill", str(member["skill_id"]), str(member.get("title", member.get("name"))), {"skill_id": member["skill_id"], "skill_name": member["name"], "aliases": [member.get("title", member["name"])]})
            self._upsert_edge(member_node, synthesis_node, "derived_from", "approved", "human-synthesis", 1.0, "", "", evidence_ids, approved_by=approval_id, version=skill.version)

    def approve_edge(self, edge_id: str, approver: str) -> None:
        if edge_id not in self.edges:
            raise KeyError(edge_id)
        self.edges[edge_id]["status"] = "approved"
        self.edges[edge_id]["approved_by"] = approver

    def validate_governance(self) -> ValidationReport:
        report = ValidationReport()
        referenced: set[str] = set()
        aliases: dict[str, set[str]] = {}
        for edge_id, edge in self.edges.items():
            for endpoint in ("from", "to"):
                node_id = edge.get(endpoint)
                if node_id not in self.nodes:
                    report.add("error", "dangling-edge", f"Edge {edge_id} 引用了不存在的 node：{node_id}")
                else:
                    referenced.add(str(node_id))
            if edge.get("status") == "candidate" and edge.get("approved_by"):
                report.add("error", "candidate-approved-by", f"Candidate edge {edge_id} 不得带 approved_by。")
        for node_id, node in self.nodes.items():
            for alias in node.get("aliases", []):
                aliases.setdefault(self._normalize_alias(str(alias)), set()).add(node_id)
            if node.get("type") not in {"book", "project"} and node_id not in referenced:
                report.add("warning", "orphan-node", f"Node {node_id} 没有任何 edge。")
        for alias, node_ids in aliases.items():
            if alias and len(node_ids) > 1:
                report.add("warning", "duplicate-alias", f"Alias {alias!r} 指向多个 node：{sorted(node_ids)}")
        pairs = {(edge["from"], edge["to"]) for edge in self.edges.values() if edge["type"] == "conflicts"}
        for source, target in pairs:
            if source == target:
                report.add("error", "self-conflict", f"Node {source} 与自身冲突。")
        return report

    def to_json(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def to_markdown(self) -> str:
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            counts[node["type"]] = counts.get(node["type"], 0) + 1
        lines = [
            "---", 'title: "Concept Graph"', 'type: "system"', 'status: "active"',
            'engine_version: "3.0.0"', "---", "", "# Concept Graph", "",
            "> 仅展示 verified/approved 边；LLM candidate 保留在 JSON 审核层，不按事实展示。", "", "## 统计", "",
        ]
        lines.extend(f"- {kind}: {count}" for kind, count in sorted(counts.items()))
        lines.extend(["", "## 已验证连接", ""])
        for edge in sorted(self.edges.values(), key=lambda item: (item["type"], item["from"], item["to"])):
            if edge["status"] not in {"verified", "approved"}:
                continue
            source = self.nodes.get(edge["from"], {}).get("title", edge["from"])
            target = self.nodes.get(edge["to"], {}).get("title", edge["to"])
            lines.append(f"- {source} → `{edge['type']}` → {target} (`{edge['status']}`)")
        return "\n".join(lines) + "\n"

    def _upsert_node(self, kind: str, identity: str, title: str, metadata: dict[str, Any]) -> str:
        node_id = f"{kind}:" + sha256_text(f"{kind}\0{identity}")[:24]
        incoming = {"id": node_id, "type": kind, "title": title, "created_at": now_iso(), **metadata}
        existing = self.nodes.get(node_id)
        if existing is None:
            self.nodes[node_id] = incoming
        else:
            for key, value in metadata.items():
                if isinstance(value, list):
                    target = existing.setdefault(key, [])
                    for item in value:
                        if item not in target:
                            target.append(item)
                elif key not in existing:
                    existing[key] = value
        return node_id

    def _upsert_edge(
        self,
        source: str,
        target: str,
        kind: str,
        status: str,
        provider: str,
        confidence: float,
        source_id: str,
        conversion_id: str,
        evidence_ids: list[str],
        statement: str | None = None,
        approved_by: str | None = None,
        version: str | None = None,
        nonce: str = "",
    ) -> str:
        normalized_statement = re.sub(r"\s+", " ", statement or "").strip()
        payload = canonical_json({
            "from": source, "to": target, "type": kind, "source_id": source_id,
            "conversion_id": conversion_id, "version": version, "nonce": nonce,
            "statement": normalized_statement,
        })
        edge_id = "edge:" + sha256_text(payload)[:24]
        record = {
            "id": edge_id, "from": source, "to": target, "type": kind,
            "status": status, "provider": provider, "confidence": max(0.0, min(1.0, float(confidence))),
            "source_id": source_id, "conversion_id": conversion_id, "evidence_ids": sorted(set(evidence_ids)),
            "created_at": now_iso(), "approved_by": approved_by, "version": version,
        }
        if normalized_statement:
            record["statement"] = normalized_statement
        prior = self.edges.get(edge_id)
        if prior is not None:
            immutable = ("from", "to", "type", "source_id", "conversion_id", "version", "statement")
            if any(prior.get(key) != record.get(key) for key in immutable):
                raise ValueError(f"Graph edge identity conflict: {edge_id}")
            if prior.get("approved_by") and approved_by and prior.get("approved_by") != approved_by:
                raise ValueError(f"Graph edge approval conflict: {edge_id}")
            prior["evidence_ids"] = sorted(set(prior.get("evidence_ids", [])) | set(record["evidence_ids"]))
            prior["provider"] = "+".join(sorted(set(str(prior.get("provider", "")).split("+")) | set(provider.split("+"))))
            prior["confidence"] = max(float(prior.get("confidence", 0.0)), record["confidence"])
            status_rank = {"rejected": 0, "candidate": 1, "verified": 2, "approved": 3, "deprecated": 4}
            prior["status"] = max((str(prior.get("status", "candidate")), status), key=lambda item: status_rank.get(item, -1))
            if not prior.get("approved_by") and approved_by:
                prior["approved_by"] = approved_by
        else:
            self.edges[edge_id] = record
        return edge_id

    @staticmethod
    def _normalize_alias(value: str) -> str:
        return re.sub(r"\s+", "", value).casefold()

    @staticmethod
    def schema() -> dict[str, Any]:
        edge_required = ["id", "from", "to", "type", "status", "provider", "confidence", "source_id", "conversion_id", "evidence_ids", "created_at", "approved_by", "version"]
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object", "additionalProperties": False,
            "required": ["schema_version", "engine_version", "revision", "nodes", "edges"],
            "properties": {
                "schema_version": {"const": 3}, "engine_version": {"type": "string"}, "revision": {"type": "integer", "minimum": 0},
                "nodes": {"type": "object", "additionalProperties": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "type", "title", "created_at"],
                    "properties": {
                        "id": {"type": "string"},
                        "type": {"enum": ["book", "concept", "method", "skill", "project"]},
                        "title": {"type": "string"},
                        "created_at": {"type": "string"},
                        "source_id": {"type": "string"},
                        "skill_id": {"type": "string"},
                        "skill_name": {"type": "string"},
                        "aliases": {"type": "array", "items": {"type": "string"}},
                    },
                }},
                "edges": {"type": "object", "additionalProperties": {
                    "type": "object", "required": edge_required,
                    "properties": {
                        "id": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"}, "type": {"type": "string"},
                        "status": {"enum": ["candidate", "verified", "approved", "rejected", "deprecated"]},
                        "provider": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "source_id": {"type": "string"}, "conversion_id": {"type": "string"},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        "created_at": {"type": "string"}, "approved_by": {"type": ["string", "null"]}, "version": {"type": ["string", "null"]},
                        "statement": {"type": "string"},
                    }, "additionalProperties": False,
                }},
            },
        }
