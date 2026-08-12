from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .evidence import EvidenceCatalog
from .identity import conversion_directory, source_directory
from .models import Analysis, CanonicalConcept, ConversionIdentity, GroundedStatement, IngestedSource, MethodCluster, ProviderResult, SkillMaterialization
from .utils import normalize_text, note_id, safe_filename, sha256_bytes, sha256_text, wikilink, yaml_frontmatter


@dataclass(frozen=True)
class VaultLayout:
    sources_books: Path = Path("06_Sources/Books")
    attachments: Path = Path("90_Attachments/Other/Books")
    cards: Path = Path("05_Knowledge/Cards")
    concepts: Path = Path("05_Knowledge/Concepts")
    methods: Path = Path("05_Knowledge/Methods")
    cases: Path = Path("05_Knowledge/Cases")
    skills: Path = Path("05_Knowledge/Skills")


@dataclass
class RenderedBundle:
    source_root: Path
    conversion_root: Path
    original_path: Path
    artifacts: dict[Path, str] = field(default_factory=dict)
    binary_artifacts: dict[Path, bytes] = field(default_factory=dict)
    skill_paths: list[Path] = field(default_factory=list)
    version_skill_paths: list[Path] = field(default_factory=list)
    knowledge_paths: list[Path] = field(default_factory=list)


class ArtifactRenderer:
    def __init__(self, layout: VaultLayout | None = None, author: str = "Andy", project: str = "建设个人知识库") -> None:
        self.layout = layout or VaultLayout()
        self.author = author
        self.project = project

    def render(
        self,
        source: IngestedSource,
        conversion: ConversionIdentity,
        analysis: Analysis,
        catalog: EvidenceCatalog,
        provider: ProviderResult,
        skills: list[SkillMaterialization],
        edition_id: str | None = None,
        method_clusters: tuple[MethodCluster, ...] = (),
        canonical_concepts: tuple[CanonicalConcept, ...] = (),
        skill_method_ids: dict[str, str] | None = None,
    ) -> RenderedBundle:
        source_root = source_directory(analysis.title, source.source_id, edition_id)
        conversion_root = conversion_directory(source_root, conversion.conversion_id)
        original_path = source_root / "originals" / f"{source.source_id[:12]}--{safe_filename(source.original_name)}"
        bundle = RenderedBundle(source_root, conversion_root, original_path)
        bundle.binary_artifacts[original_path] = source.local_path.read_bytes()
        source_text_path = conversion_root / "SOURCE_TEXT.md"
        evidence_path = conversion_root / "EVIDENCE_CATALOG.json"
        bundle.artifacts[source_text_path] = normalize_text(source.text)
        bundle.artifacts[evidence_path] = catalog.to_json()
        bundle.artifacts[conversion_root / "CONVERSION.json"] = json.dumps({
            "schema_version": 4, "engine_version": "3.3.0", **conversion.as_dict(), "source_title": analysis.title,
            "original_hash": source.original_hash,
            "original_path": original_path.as_posix(), "source_text_path": source_text_path.as_posix(),
            "source_text_sha256": sha256_text(normalize_text(source.text)),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        overview = conversion_root / "BOOK_OVERVIEW.md"
        digest = conversion_root / "DIGEST.md"
        bundle.artifacts[overview] = self._overview(source, conversion, analysis, provider, original_path, digest, skills)
        bundle.artifacts[digest] = self._digest(source, conversion, analysis, original_path, overview)
        bundle.knowledge_paths.extend([overview, digest, evidence_path])

        unique = f"{safe_filename(analysis.title)}--{source.source_id[:8]}"
        for index, evidence in enumerate(analysis.chapter_evidence[:20], start=1):
            path = self.layout.cards / unique / f"{index:02d}-{safe_filename(evidence.section)}.md"
            bundle.artifacts[path] = self._topic_card(source, conversion, analysis, evidence, overview, original_path)
            bundle.knowledge_paths.append(path)
        concept_paths_by_title: dict[str, Path] = {}
        for concept in provider.concepts:
            path = self.layout.concepts / f"{unique}--{safe_filename(concept.title)}.md"
            bundle.artifacts[path] = self._concept_note(source, conversion, analysis, concept, catalog, overview, original_path)
            bundle.knowledge_paths.append(path)
            concept_paths_by_title[concept.title.casefold()] = path
        for method in analysis.methods:
            path = self.layout.methods / f"{unique}--{safe_filename(method.title)}.md"
            bundle.artifacts[path] = self._method_note(source, conversion, analysis, method, catalog, overview, original_path)
            bundle.knowledge_paths.append(path)
        canonical_by_id = {item.canonical_concept_id: item for item in canonical_concepts}
        method_paths: dict[str, Path] = {}
        skill_path_by_method = {
            method_id: self.layout.skills / skill.name / "SKILL.md"
            for skill in skills
            if (method_id := (skill_method_ids or {}).get(skill.candidate.fingerprint()))
        }
        for cluster in method_clusters:
            path = self.layout.methods / f"{unique}--{safe_filename(cluster.title)}--{cluster.id[-8:]}.md"
            method_paths[cluster.id] = path
            concept_paths = [
                concept_paths_by_title[canonical_by_id[item].canonical_title.casefold()]
                for item in cluster.concept_ids
                if item in canonical_by_id and canonical_by_id[item].canonical_title.casefold() in concept_paths_by_title
            ]
            bundle.artifacts[path] = self._method_cluster_note(
                source, conversion, analysis, cluster, catalog, overview, original_path,
                concept_paths, skill_path_by_method.get(cluster.id),
            )
            bundle.knowledge_paths.append(path)
        for case in analysis.cases:
            path = self.layout.cases / f"{unique}--{safe_filename(case.title)}.md"
            bundle.artifacts[path] = self._case_note(source, conversion, analysis, case, catalog, overview, original_path)
            bundle.knowledge_paths.append(path)

        for skill in skills:
            root = self.layout.skills / skill.name
            version_root = root / "versions" / skill.version
            method_id = (skill_method_ids or {}).get(skill.candidate.fingerprint())
            cluster = next((item for item in method_clusters if item.id == method_id), None)
            concept_paths = [] if cluster is None else [
                concept_paths_by_title[canonical_by_id[item].canonical_title.casefold()]
                for item in cluster.concept_ids
                if item in canonical_by_id and canonical_by_id[item].canonical_title.casefold() in concept_paths_by_title
            ]
            skill_text = self.skill_text(
                skill, catalog, overview, original_path, cluster,
                method_paths.get(method_id or ""), tuple(concept_paths),
            )
            evidence_text = self.skill_evidence_json(skill, catalog)
            agent_text = self.openai_yaml(skill)
            version_record_text = json.dumps({
                "schema_version": 4, "engine_version": "3.3.0", "skill_id": skill.skill_id, "name": skill.name,
                "version": skill.version, "status": skill.status,
                "skill_sha256": sha256_text(skill_text), "evidence_sha256": sha256_text(evidence_text),
                "source_id": skill.source_id, "conversion_id": skill.conversion_id,
            }, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            for base in (root, version_root):
                bundle.artifacts[base / "SKILL.md"] = skill_text
                bundle.artifacts[base / "agents" / "openai.yaml"] = agent_text
                bundle.artifacts[base / "references" / "evidence.json"] = evidence_text
            bundle.artifacts[version_root / "VERSION.json"] = version_record_text
            bundle.skill_paths.append(root / "SKILL.md")
            bundle.version_skill_paths.append(version_root / "SKILL.md")

        manifest_path = conversion_root / "DISTILLATION_MANIFEST.json"
        bundle.artifacts[manifest_path] = self._manifest(source, conversion, analysis, provider, original_path, bundle)
        bundle.knowledge_paths.append(manifest_path)
        return bundle

    def render_blocked(self, source: IngestedSource, conversion: ConversionIdentity, analysis: Analysis, reason: str, edition_id: str | None = None) -> RenderedBundle:
        source_root = source_directory(analysis.title, source.source_id, edition_id)
        conversion_root = conversion_directory(source_root, conversion.conversion_id)
        original_path = source_root / "originals" / f"{source.source_id[:12]}--{safe_filename(source.original_name)}"
        bundle = RenderedBundle(source_root, conversion_root, original_path)
        bundle.binary_artifacts[original_path] = source.local_path.read_bytes()
        bundle.artifacts[conversion_root / "SOURCE_TEXT.md"] = normalize_text(source.text)
        bundle.artifacts[conversion_root / "CONVERSION.json"] = json.dumps({
            "schema_version": 3, **conversion.as_dict(), "status": "blocked", "reason": reason,
            "original_path": original_path.as_posix(), "source_text_sha256": sha256_text(normalize_text(source.text)),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        bundle.artifacts[conversion_root / "BLOCKED.md"] = f"# Conversion Blocked\n\n{reason}\n"
        return bundle

    def skill_text(
        self,
        skill: SkillMaterialization,
        catalog: EvidenceCatalog,
        overview_path: Path,
        original_path: Path,
        method_cluster: MethodCluster | None = None,
        method_path: Path | None = None,
        concept_paths: tuple[Path, ...] = (),
    ) -> str:
        candidate = skill.candidate
        description = (
            f"Apply {candidate.title} to evidence-grounded tasks. Use when the user explicitly needs this method and supplies its required inputs. "
            "Do not use for unrelated requests, missing critical inputs, or claims beyond the bound source Evidence. "
            "On Windows PowerShell, read this UTF-8 SKILL.md with -Encoding UTF8."
        )
        frontmatter = yaml_frontmatter({"name": skill.name, "description": description})
        invocation_fingerprint = sha256_text(f"{skill.skill_id}\0{skill.version}\0{candidate.fingerprint()}")[:20]
        body = [
            f"# {candidate.title}", "", f"> Skill ID: `{skill.skill_id}` · Version: `{skill.version}` · Invocation fingerprint: `{invocation_fingerprint}`", "",
            "## 1. Skill定位", "", self._marked(candidate.positioning), "",
            "## 2. 适用场景", "", self._bullets(candidate.scenarios), "",
            "## 3. 输入要求", "", self._bullets(candidate.input_requirements), "",
            "## 4. 判断流程", "", self._numbered(candidate.decision_flow), "",
            "## 5. 执行动作", "", self._numbered(candidate.actions), "",
            "## 6. 输出格式", "", self._marked(candidate.output_format), "",
            "## 7. 使用边界", "", self._bullets(candidate.boundaries), "",
            "## 8. 禁止场景", "", self._bullets(candidate.prohibited_scenarios), "",
            "## 9. 原文证据", "", self._evidence_table(self._evidence_ids(candidate.source_statements()), catalog), "",
            "## 来源入口", "", f"- {wikilink(overview_path, skill.source_title)}", f"- {wikilink(original_path, '原件')}",
            *([f"- Method: {wikilink(method_path, method_cluster.title)}"] if method_cluster and method_path else []),
            *(f"- Concept: {wikilink(path)}" for path in concept_paths), "",
            "Engine policy 陈述只用于执行安全与格式控制，已明确标注，不代表原文观点。", "",
        ]
        return frontmatter + "\n".join(body)

    @staticmethod
    def openai_yaml(skill: SkillMaterialization) -> str:
        display = skill.candidate.title[:48]
        short = f"Apply {skill.candidate.title} with source-bound Evidence"[:64]
        if len(short) < 25:
            short = (short + " for grounded decisions")[:64]
        return (
            "interface:\n"
            f"  display_name: {json.dumps(display, ensure_ascii=False)}\n"
            f"  short_description: {json.dumps(short, ensure_ascii=False)}\n"
            f"  default_prompt: {json.dumps(f'Use ${skill.name} to apply the approved evidence-bound method. On Windows PowerShell, read SKILL.md with -Encoding UTF8.', ensure_ascii=False)}\n"
            "policy:\n"
            "  allow_implicit_invocation: false\n"
        )

    @staticmethod
    def skill_evidence_json(skill: SkillMaterialization, catalog: EvidenceCatalog) -> str:
        ids = set(ArtifactRenderer._evidence_ids(skill.candidate.source_statements()))
        payload = {
            "schema_version": 3, "skill_id": skill.skill_id, "skill_name": skill.name,
            "skill_version": skill.version, "source_id": catalog.source_id,
            "conversion_id": catalog.conversion_id,
            "evidence": [item.as_dict() for item in catalog.items.values() if item.evidence_id in ids],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def _overview(self, source: IngestedSource, conversion: ConversionIdentity, analysis: Analysis, provider: ProviderResult, original_path: Path, digest_path: Path, skills: list[SkillMaterialization]) -> str:
        frontmatter = self._note_frontmatter(note_id("source", source.source_id), analysis.title, "source", source, conversion, "processed", [wikilink(original_path)], [wikilink(digest_path)])
        lines = [frontmatter, f"# {analysis.title}", "", f"- Source ID: `{source.source_id}`", f"- Conversion ID: `{conversion.conversion_id}`", f"- Analyzer: `{provider.provider}`", f"- Original: {wikilink(original_path)}", "", "## 章节", ""]
        lines.extend(f"- {item.title}" for item in analysis.sections) or lines.append("- 未识别明确章节。")
        lines.extend(["", "## Skill Candidates", ""])
        lines.extend(f"- {wikilink(Path('05_Knowledge/Skills') / item.name / 'SKILL.md', item.candidate.title)} · `{item.status}`" for item in skills) or lines.append("- 无。")
        return "\n".join(lines) + "\n"

    def _digest(self, source: IngestedSource, conversion: ConversionIdentity, analysis: Analysis, original_path: Path, overview_path: Path) -> str:
        frontmatter = self._note_frontmatter(note_id("digest", conversion.conversion_id), analysis.title + " Digest", "digest", source, conversion, "candidate", [wikilink(original_path)], [wikilink(overview_path)])
        lines = [frontmatter, f"# {analysis.title} · Digest", "", "> 仅收录可定位到本 conversion snapshot 的结构化候选。", "", "## 核心概念", ""]
        lines.extend(f"- {item.statement} · `{item.evidence.locator}`" for item in analysis.concepts) or lines.append("- 无。")
        lines.extend(["", "## 方法", ""])
        lines.extend(f"- {item.title}: {'；'.join(item.steps)}" for item in analysis.methods) or lines.append("- 无。")
        return "\n".join(lines) + "\n"

    def _topic_card(self, source: IngestedSource, conversion: ConversionIdentity, analysis: Analysis, evidence, overview_path: Path, original_path: Path) -> str:  # type: ignore[no-untyped-def]
        frontmatter = self._note_frontmatter(note_id("card", evidence.evidence_id), f"{analysis.title} - {evidence.section}", "knowledge-card", source, conversion, "candidate", [wikilink(original_path)], [wikilink(overview_path)])
        return frontmatter + f"# {evidence.section}\n\n{evidence.excerpt}\n\n- Evidence: `{evidence.evidence_id}`\n- Locator: `{evidence.locator}`\n"

    def _concept_note(self, source: IngestedSource, conversion: ConversionIdentity, analysis: Analysis, concept, catalog: EvidenceCatalog, overview_path: Path, original_path: Path) -> str:  # type: ignore[no-untyped-def]
        frontmatter = self._note_frontmatter(note_id("concept", source.source_id, concept.title), concept.title, "concept", source, conversion, "candidate", [wikilink(original_path)], [wikilink(overview_path)])
        return frontmatter + f"# {concept.title}\n\n{self._marked(concept.definition)}\n\n{self._evidence_table(concept.definition.evidence_ids, catalog)}\n"

    def _method_note(self, source: IngestedSource, conversion: ConversionIdentity, analysis: Analysis, method, catalog: EvidenceCatalog, overview_path: Path, original_path: Path) -> str:  # type: ignore[no-untyped-def]
        frontmatter = self._note_frontmatter(note_id("method", source.source_id, method.title), method.title, "method", source, conversion, "candidate", [wikilink(original_path)], [wikilink(overview_path)])
        ids = [eid for item in method.evidence for eid in catalog.ids_for_evidence(item)]
        return frontmatter + f"# {method.title}\n\n" + "\n".join(f"{i}. {step}" for i, step in enumerate(method.steps, 1)) + f"\n\n{self._evidence_table(ids, catalog)}\n"

    def _method_cluster_note(
        self,
        source: IngestedSource,
        conversion: ConversionIdentity,
        analysis: Analysis,
        cluster: MethodCluster,
        catalog: EvidenceCatalog,
        overview_path: Path,
        original_path: Path,
        concept_paths: list[Path],
        skill_path: Path | None,
    ) -> str:
        related = [wikilink(overview_path), *(wikilink(path) for path in concept_paths)]
        if skill_path:
            related.append(wikilink(skill_path))
        frontmatter = self._note_frontmatter(
            cluster.id, cluster.title, "method", source, conversion, "candidate",
            [wikilink(original_path)], related,
        )
        lines = [
            frontmatter, f"# {cluster.title}", "", cluster.description, "",
            f"- Method Cluster ID: `{cluster.id}`",
            f"- Confidence: `{cluster.confidence:.4f}`",
            f"- Goal: {cluster.cohesion_assessment.get('goal', cluster.description)}",
            f"- Cohesion decision: `{cluster.cohesion_assessment.get('decision', 'unknown')}`",
            f"- Strong signals: `{', '.join(cluster.cohesion_assessment.get('strong_signals', []))}`",
            f"- Creation reason: `{cluster.creation_reason}`",
            f"- Conflicts: `{json.dumps(cluster.cohesion_assessment.get('conflicts', []), ensure_ascii=False)}`",
            "", "## Cohesion Assessment", "",
            "```json",
            json.dumps(cluster.cohesion_assessment, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "", "## Concepts", "",
        ]
        lines.extend(f"- {wikilink(path)}" for path in concept_paths) or lines.append("- 无可用 Concept 链接；保持 Candidate Blocked。")
        lines.extend(["", "## Candidate Skill", ""])
        lines.append(f"- {wikilink(skill_path)}" if skill_path else "- 未通过 Skill Synthesis Gate。")
        lines.extend(["", "## Evidence", "", self._evidence_table(cluster.evidence_ids, catalog), ""])
        return "\n".join(lines)

    def _case_note(self, source: IngestedSource, conversion: ConversionIdentity, analysis: Analysis, case, catalog: EvidenceCatalog, overview_path: Path, original_path: Path) -> str:  # type: ignore[no-untyped-def]
        frontmatter = self._note_frontmatter(note_id("case", source.source_id, case.title), case.title, "case", source, conversion, "candidate", [wikilink(original_path)], [wikilink(overview_path)])
        ids = catalog.ids_for_evidence(case.evidence)
        return frontmatter + f"# {case.title}\n\n{case.statement}\n\n{self._evidence_table(ids, catalog)}\n"

    def _manifest(self, source: IngestedSource, conversion: ConversionIdentity, analysis: Analysis, provider: ProviderResult, original_path: Path, bundle: RenderedBundle) -> str:
        def immutable(path: Path) -> bool:
            value = path.as_posix()
            return not (
                value.startswith("05_Knowledge/Skills/")
                and "/versions/" not in value
            )

        artifact_hashes = {
            path.as_posix(): sha256_text(content)
            for path, content in bundle.artifacts.items() if immutable(path)
        }
        artifact_hashes.update({
            path.as_posix(): sha256_bytes(content)
            for path, content in bundle.binary_artifacts.items()
        })
        payload = {
            "schema_version": 5, "engine_version": "3.3.0", "title": analysis.title,
            "source_id": source.source_id, "conversion_id": conversion.conversion_id,
            "original_path": original_path.as_posix(), "original_sha256": source.original_hash,
            "source_text_sha256": conversion.normalized_text_sha256,
            "provider": provider.provider, "provider_provenance": provider.provenance,
            "warnings": [*source.warnings, *provider.warnings],
            "artifacts": sorted(path.as_posix() for path in bundle.artifacts),
            "artifact_hashes": artifact_hashes,
            "skills": [path.as_posix() for path in bundle.version_skill_paths],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def _note_frontmatter(self, identifier: str, title: str, note_type: str, source: IngestedSource, conversion: ConversionIdentity, status: str, source_links: list[str], related_notes: list[str]) -> str:
        return yaml_frontmatter({
            "id": identifier, "title": title, "type": note_type, "status": status,
            "source_id": source.source_id, "conversion_id": conversion.conversion_id,
            "source": source_links, "source_type": source.source_kind,
            "related_notes": related_notes, "related_projects": [self.project],
            "ai_generated": True, "confidence": "low" if source.warnings else "medium",
        })

    @staticmethod
    def _marked(statement: GroundedStatement) -> str:
        if statement.origin == "engine_policy":
            return f"{statement.text} `engine_policy`"
        ids = ", ".join(f"`{item}`" for item in statement.evidence_ids)
        return f"{statement.text} · Evidence: {ids}"

    @classmethod
    def _bullets(cls, statements: Iterable[GroundedStatement]) -> str:
        return "\n".join(f"- {cls._marked(item)}" for item in statements) or "- 无。"

    @classmethod
    def _numbered(cls, statements: Iterable[GroundedStatement]) -> str:
        return "\n".join(f"{index}. {cls._marked(item)}" for index, item in enumerate(statements, 1)) or "1. 无。"

    @staticmethod
    def _evidence_ids(statements: Iterable[GroundedStatement]) -> list[str]:
        return sorted({item for statement in statements for item in statement.evidence_ids})

    @staticmethod
    def _evidence_table(evidence_ids: Iterable[str], catalog: EvidenceCatalog) -> str:
        lines = ["| Evidence | Source | Conversion | Locator | Excerpt |", "|---|---|---|---|---|"]
        for evidence_id in sorted(set(evidence_ids)):
            item = catalog.get(evidence_id)
            if item:
                excerpt = item.excerpt.replace("|", "\\|")
                lines.append(f"| `{item.evidence_id}` | `{item.source_id}` | `{item.conversion_id}` | `{item.locator}` | {excerpt} |")
        return "\n".join(lines)
