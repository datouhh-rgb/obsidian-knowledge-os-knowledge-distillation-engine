from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .analyzer import BookAnalyzer
from .chunking import ChapterChunker, ChunkRunResult, ProviderMapReducer, ResumableChunkRunner
from .concepts import BookKnowledgeGate, BookQualityAssessment, CanonicalizationResult, ConceptCanonicalizer, ConceptQualityGate
from .conversion_quality import ConversionQualityAttestation, ConversionQualityGate
from .evidence import EvidenceCatalog, EvidenceGate
from .graph import GRAPH_JSON, GRAPH_MD, ConceptGraph
from .gates import SkillExecutabilityGate, SkillStructureGate
from .identity import bind_source_identity, build_source_identity, conversion_identity, source_directory
from .ingest import SourceIngester
from .knowledge_synthesis import KnowledgeSynthesizer, KnowledgeSynthesisResult
from .ledger import ASSET_LEDGER_JSON, AssetLedger
from .models import Analysis, CanonicalConcept, ConceptRelationCandidate, ConversionIdentity, IngestedSource, ProviderResult, SkillMaterialization, ValidationReport
from .production_gates import SkillProductionGate
from .providers import AnalyzerProvider, RuleAnalyzerProvider
from .quality import SkillQualityEvaluator
from .registry import REGISTRY_JSON, REGISTRY_MD, SkillRegistry
from .renderer import ArtifactRenderer, RenderedBundle
from .semantic import SemanticSupportGate
from .synthesis import PROPOSALS_JSON, SynthesisRegistry
from .structure import NormalizedDocument, RealStructureNormalizer
from .transaction import VaultTransaction
from .utils import atomic_write, now_iso, safe_filename, sha256_file, sha256_text
from .validator import SkillValidator


SOURCE_INDEX = Path("06_Sources/Books/SOURCE_INDEX.json")


class PipelineError(RuntimeError):
    pass


class ConversionBlocked(PipelineError):
    def __init__(self, message: str, written_paths: list[Path]) -> None:
        super().__init__(message)
        self.written_paths = written_paths


@dataclass
class DistillationResult:
    title: str
    source_id: str
    conversion_id: str
    status: str
    provider: str
    vault: Path
    source_root: Path
    written_paths: list[Path] = field(default_factory=list)
    planned_paths: list[Path] = field(default_factory=list)
    skills: list[Path] = field(default_factory=list)
    knowledge_assets: list[Path] = field(default_factory=list)
    validation: ValidationReport = field(default_factory=ValidationReport)
    git_status: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    graph_nodes: int = 0
    graph_edges: int = 0
    synthesis_proposals: list[str] = field(default_factory=list)
    chunk_run: dict[str, Any] | None = None
    method_clusters: list[str] = field(default_factory=list)
    concept_gate: dict[str, object] = field(default_factory=dict)
    conversion_quality: dict[str, object] = field(default_factory=dict)
    conversion_provenance: dict[str, object] = field(default_factory=dict)

    @property
    def source_sha256(self) -> str:
        return self.source_id

    @property
    def book_root(self) -> Path:
        return self.source_root

    def as_dict(self) -> dict[str, object]:
        return {
            "title": self.title, "source_id": self.source_id, "source_sha256": self.source_id,
            "conversion_id": self.conversion_id, "status": self.status, "provider": self.provider,
            "vault": str(self.vault), "source_root": str(self.source_root), "book_root": str(self.source_root),
            "written_paths": [str(path) for path in self.written_paths],
            "planned_paths": [str(path) for path in self.planned_paths],
            "skills": [str(path) for path in self.skills],
            "knowledge_assets": [str(path) for path in self.knowledge_assets],
            "graph": {"nodes": self.graph_nodes, "edges": self.graph_edges},
            "synthesis_proposals": self.synthesis_proposals,
            "method_clusters": self.method_clusters,
            "concept_gate": self.concept_gate,
            "conversion_quality": self.conversion_quality,
            "conversion_provenance": self.conversion_provenance,
            "chunk_run": self.chunk_run,
            "validation": {"passed": self.validation.passed, "issues": [issue.__dict__ for issue in self.validation.issues]},
            "git_status": self.git_status, "warnings": self.warnings,
        }


@dataclass(frozen=True)
class InspectionResult:
    source: IngestedSource
    conversion: ConversionIdentity
    analysis: Analysis
    catalog: EvidenceCatalog
    provider_result: ProviderResult
    validation: ValidationReport


class DistillationPipeline:
    MANAGED_MUTABLE = {REGISTRY_JSON, REGISTRY_MD, GRAPH_JSON, GRAPH_MD, PROPOSALS_JSON, SOURCE_INDEX, ASSET_LEDGER_JSON}

    def __init__(
        self,
        vault: Path,
        author: str = "Andy",
        project: str = "建设个人知识库",
        pdf_wrapper: Path | None = None,
        provider: AnalyzerProvider | None = None,
        url_allowlist: tuple[str, ...] = (),
        chunk_chars: int = 12_000,
        chunk_overlap: int = 300,
        azw3_command: tuple[str, ...] | None = None,
    ) -> None:
        self.vault = vault.resolve()
        self.author = author
        self.project = project
        self.pdf_wrapper = pdf_wrapper
        self.url_allowlist = url_allowlist
        self.azw3_command = azw3_command
        self.analyzer = BookAnalyzer()
        self.provider = provider or RuleAnalyzerProvider()
        self.renderer = ArtifactRenderer(author=author, project=project)
        self.validator = SkillValidator()
        self.evidence_gate = EvidenceGate()
        self.semantic_gate = SemanticSupportGate()
        self.structure_gate = SkillStructureGate()
        self.executability_gate = SkillExecutabilityGate()
        self.quality = SkillQualityEvaluator()
        self.concept_canonicalizer = ConceptCanonicalizer()
        self.concept_quality = ConceptQualityGate()
        self.book_knowledge_gate = BookKnowledgeGate()
        self.knowledge_synthesizer = KnowledgeSynthesizer()
        self.skill_production_gate = SkillProductionGate()
        self.structure_normalizer = RealStructureNormalizer()
        self.conversion_quality_gate = ConversionQualityGate()
        self.chunker = ChapterChunker(chunk_chars, chunk_overlap)

    def distill(
        self,
        source_ref: str,
        title: str | None = None,
        update: bool = False,
        allow_warnings: bool = False,
        dry_run: bool = False,
        edition_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> DistillationResult:
        del update  # V3 immutable identities replace V2 overwrite mode.
        self._validate_existing_vault()
        with tempfile.TemporaryDirectory(prefix="knowledge-engine-v3-") as temporary:
            source = SourceIngester(
                Path(temporary), self.pdf_wrapper, url_allowlist=self.url_allowlist,
                azw3_command=self.azw3_command,
            ).ingest(source_ref)
            bind_source_identity(source, edition_id)
            conversion_quality = self.conversion_quality_gate.validate(source)
            if not conversion_quality.passed:
                provisional_conversion = conversion_identity(source, source.text)
                conversion_quality = conversion_quality.bind(provisional_conversion.conversion_id)
                self._write_conversion_quality_diagnostic(source, conversion_quality)
                validation = ValidationReport()
                for issue in conversion_quality.issues:
                    validation.add(
                        "error" if issue.severity == "blocker" else "warning",
                        "conversion-quality-" + issue.code,
                        issue.message,
                        issue.member or issue.locator,
                    )
                blocked_title = title or Path(source.original_name).stem or "untitled-source"
                return DistillationResult(
                    blocked_title,
                    source.source_id,
                    provisional_conversion.conversion_id,
                    "conversion-quality-blocked",
                    "blocked-before-llm",
                    self.vault,
                    self.vault / source_directory(blocked_title, source.source_id, edition_id),
                    validation=validation,
                    warnings=[*source.warnings, "CONVERSION_QUALITY_BLOCKED"],
                    conversion_quality=conversion_quality.as_dict(),
                    conversion_provenance=dict(source.converter_metadata),
                )
            normalized_document = self.structure_normalizer.normalize(source.text, source.source_kind)
            source.text = normalized_document.text
            source.warnings.extend(item for item in normalized_document.warnings if item not in source.warnings)
            conversion = conversion_identity(source, normalized_document.text)
            conversion_quality = conversion_quality.bind(conversion.conversion_id)
            source_identity = build_source_identity(source, conversion, edition_id)
            analysis = self.analyzer.analyze(source.text, title)
            source_root = source_directory(analysis.title, source.source_id, edition_id)
            original_path = source_root / "originals" / f"{source.source_id[:12]}--{safe_filename(source.original_name)}"
            catalog = EvidenceCatalog.from_text(
                analysis.title, source.source_id, source.text, conversion.conversion_id, original_path.as_posix(),
                conversion_locator_for=normalized_document.conversion_locator_for,
            )
            chunk_run, provider_result = self._run_chunks(source, conversion, analysis.title, catalog)
            if chunk_run.status != "completed":
                validation = ValidationReport()
                reason = "长文档分块提取未完成；未生成 completed 知识资产。"
                code = "evidence-selection-error" if chunk_run.status == "blocked" else "chunk-extraction-interrupted"
                validation.add("error", code, "; ".join(chunk_run.warnings) or reason, chunk_run.failed_chunk or "")
                return self._archive_blocked(
                    source, conversion, analysis, reason, edition_id, dry_run, validation, chunk_run,
                    conversion_quality=conversion_quality,
                )
            canonicalization = self.concept_canonicalizer.canonicalize(provider_result.concepts, source.source_id)
            concept_quality = self.concept_quality.validate(
                canonicalization.candidates, (section.title for section in analysis.sections),
            )
            book_quality = self.book_knowledge_gate.validate(concept_quality)
            accepted = {self.concept_canonicalizer.normalized_title(item.title): item for item in concept_quality.accepted}
            conflicted_titles = {item.normalized_title for item in canonicalization.conflicts}
            stable_canonical = tuple(
                item for item in canonicalization.concepts
                if self.concept_canonicalizer.normalized_title(item.canonical_title) in accepted
                and self.concept_canonicalizer.normalized_title(item.canonical_title) not in conflicted_titles
            )
            aliases = {
                self.concept_canonicalizer.normalized_title(alias): item.canonical_title
                for item in stable_canonical for alias in item.aliases
            }
            relations: list[ConceptRelationCandidate] = []
            for relation in provider_result.relations:
                source_title = aliases.get(self.concept_canonicalizer.normalized_title(relation.source_concept))
                target_title = aliases.get(self.concept_canonicalizer.normalized_title(relation.target_concept))
                if source_title and target_title and source_title.casefold() != target_title.casefold():
                    relations.append(ConceptRelationCandidate(
                        source_title, target_title, relation.relation, relation.provider, relation.confidence,
                    ))
            provider_result.concepts = [item.as_candidate() for item in stable_canonical]
            provider_result.relations = relations
            original_skill_fingerprints = {item.fingerprint() for item in provider_result.skills}
            knowledge_synthesis = self.knowledge_synthesizer.synthesize(
                stable_canonical, catalog, analysis.title,
            )
            known_fingerprints = set(original_skill_fingerprints)
            for candidate in knowledge_synthesis.skills:
                if candidate.fingerprint() not in known_fingerprints:
                    provider_result.skills.append(candidate)
                    known_fingerprints.add(candidate.fingerprint())
            provider_result.provenance = {
                **provider_result.provenance,
                "knowledge_synthesis": {
                    "provider": KnowledgeSynthesizer.VERSION,
                    "method_clusters": len(knowledge_synthesis.method_clusters),
                    "skill_candidates": len(knowledge_synthesis.skills),
                    "blocked_candidates": len(knowledge_synthesis.blocked),
                },
                "concept_quality": book_quality.as_dict(),
            }
            structural = ValidationReport()
            knowledge_only = ProviderResult(
                provider=provider_result.provider,
                concepts=list(provider_result.concepts), relations=list(provider_result.relations), skills=[],
                warnings=list(provider_result.warnings), provenance=dict(provider_result.provenance),
            )
            structural.issues.extend(self.evidence_gate.validate(knowledge_only, catalog).issues)
            structural.issues.extend(self.concept_quality.report(concept_quality).issues)
            if not book_quality.passed:
                structural.add(
                    "error", "book-knowledge-gate", book_quality.block_reason,
                    analysis.title,
                )
            for conflict in canonicalization.conflicts:
                structural.add(
                    "warning", "unresolved-concept-conflict-isolated",
                    f"{conflict.reason}; all variants were excluded from Method/Skill synthesis.",
                    conflict.conflict_id,
                )
            base_graph = ConceptGraph.load(self.vault)
            graph_dry_run = base_graph.dry_run_distillation(
                analysis.title, source.source_id, conversion.conversion_id, analysis, catalog, knowledge_only, [],
                tuple(knowledge_synthesis.method_clusters), stable_canonical, knowledge_synthesis.skill_method_ids,
            )
            for issue in graph_dry_run.issues:
                structural.add("error", issue["code"], issue["message"])
            semantic_by_fingerprint: dict[str, Any] = {}
            preliminary_scores: dict[str, Any] = {}
            production_candidates = []
            for candidate in provider_result.skills:
                is_synthesized = candidate.fingerprint() in knowledge_synthesis.skill_method_ids
                semantic = self.semantic_gate.assess_skill(candidate, catalog)
                semantic_by_fingerprint[candidate.fingerprint()] = semantic
                score = self.quality.score(candidate, catalog, {}, semantic.assessments, source.warnings)
                preliminary_scores[candidate.fingerprint()] = score
                candidate_gate = self.skill_production_gate.validate(
                    candidate, catalog,
                    concepts_stable=bool(stable_canonical) if is_synthesized else bool(stable_canonical) and not canonicalization.conflicts,
                    graph_dry_run=graph_dry_run,
                    conflicts=() if is_synthesized else canonicalization.conflicts,
                    quality_score=score,
                )
                candidate_errors = [*semantic.report.errors, *candidate_gate.errors]
                if candidate_errors:
                    method_id = knowledge_synthesis.skill_method_ids.pop(candidate.fingerprint(), None)
                    knowledge_synthesis.blocked.append({
                        **({"method_cluster_id": method_id} if method_id else {}),
                        "skill_fingerprint": candidate.fingerprint(),
                        "reasons": [f"{item.code}: {item.message}" for item in candidate_errors],
                    })
                    structural.add(
                        "warning", "skill-candidate-quarantined",
                        "; ".join(f"{item.code}: {item.message}" for item in candidate_errors),
                        candidate.title,
                    )
                    continue
                structural.issues.extend(semantic.report.issues)
                structural.issues.extend(candidate_gate.issues)
                production_candidates.append(candidate)
            provider_result.skills = production_candidates
            knowledge_synthesis.skills = [
                item for item in knowledge_synthesis.skills
                if item.fingerprint() in knowledge_synthesis.skill_method_ids
            ]
            if structural.errors:
                reason = "; ".join(f"{issue.code}: {issue.message}" for issue in structural.errors)
                return self._archive_blocked(source, conversion, analysis, reason, edition_id, dry_run, structural, chunk_run, book_quality, conversion_quality)

            if source.warnings and not allow_warnings:
                # Warnings are archived as review snapshots, never silently promoted.
                provider_result.warnings.append("conversion warning: candidate lifecycle remains review/blocked regardless of --allow-warnings")

            with VaultTransaction(
                self.vault, "distill", idempotency_key=idempotency_key,
                request_payload={
                    "source_id": source.source_id,
                    "conversion_id": conversion.conversion_id,
                    "title": analysis.title,
                    "edition_id": edition_id,
                    "provider": provider_result.provider,
                    "allow_warnings": allow_warnings,
                },
            ) as tx:
                if tx.duplicate:
                    prior = tx.prior_event or {}
                    return DistillationResult(
                        analysis.title, source.source_id, conversion.conversion_id, "duplicate", provider_result.provider,
                        self.vault, self.vault / source_root, warnings=list(source.warnings), chunk_run=self._chunk_dict(chunk_run),
                    )
                registry = SkillRegistry.load(self.vault)
                graph = ConceptGraph.load(self.vault)
                synthesis = SynthesisRegistry.load(self.vault)
                ledger = AssetLedger.load(self.vault)
                skills: list[SkillMaterialization] = []
                for candidate in provider_result.skills:
                    is_synthesized = candidate.fingerprint() in knowledge_synthesis.skill_method_ids
                    semantic = semantic_by_fingerprint[candidate.fingerprint()]
                    score = self.quality.score(candidate, catalog, registry.records, semantic.assessments, source.warnings)
                    gate = self.skill_production_gate.validate(
                        candidate, catalog,
                        concepts_stable=bool(stable_canonical) if is_synthesized else bool(stable_canonical) and not canonicalization.conflicts,
                        graph_dry_run=graph_dry_run,
                        conflicts=() if is_synthesized else canonicalization.conflicts,
                        quality_score=score,
                    )
                    if gate.errors:
                        structural.issues.extend(gate.issues)
                        reason = "; ".join(f"{issue.code}: {issue.message}" for issue in gate.errors)
                        return self._archive_blocked(source, conversion, analysis, reason, edition_id, dry_run, structural, chunk_run, book_quality, conversion_quality)
                    item = registry.create_source_candidate(
                        candidate, source, conversion, analysis.title, score, semantic.assessments,
                        provider_result.provenance or {"provider": provider_result.provider, "rule_version": "rule-v3"},
                    )
                    skills.append(item)
                final_graph_dry_run = graph.dry_run_distillation(
                    analysis.title, source.source_id, conversion.conversion_id, analysis, catalog, provider_result, skills,
                    tuple(knowledge_synthesis.method_clusters), stable_canonical, knowledge_synthesis.skill_method_ids,
                )
                if not final_graph_dry_run.passed or final_graph_dry_run.graph is None:
                    reason = "; ".join(item["message"] for item in final_graph_dry_run.issues) or "Graph dry-run failed."
                    structural.add("error", "graph-dry-run", reason)
                    return self._archive_blocked(source, conversion, analysis, reason, edition_id, dry_run, structural, chunk_run, book_quality, conversion_quality)
                graph = final_graph_dry_run.graph
                bundle = self.renderer.render(
                    source, conversion, analysis, catalog, provider_result, skills, edition_id,
                    tuple(knowledge_synthesis.method_clusters), stable_canonical,
                    knowledge_synthesis.skill_method_ids,
                )
                bundle.artifacts[bundle.conversion_root / "SOURCE_IDENTITY.json"] = json.dumps(source_identity.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                bundle.artifacts[bundle.conversion_root / "STRUCTURE_NORMALIZATION.json"] = json.dumps(normalized_document.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                bundle.artifacts[bundle.conversion_root / "CONVERSION_QUALITY.json"] = json.dumps(
                    conversion_quality.as_dict(), ensure_ascii=False, indent=2, sort_keys=True,
                ) + "\n"
                bundle.artifacts[bundle.conversion_root / "CONCEPT_CANONICALIZATION.json"] = json.dumps(canonicalization.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                bundle.artifacts[bundle.conversion_root / "KNOWLEDGE_SYNTHESIS.json"] = json.dumps(
                    knowledge_synthesis.as_dict(), ensure_ascii=False, indent=2, sort_keys=True,
                ) + "\n"
                manifest_path = bundle.conversion_root / "DISTILLATION_MANIFEST.json"
                bundle.artifacts.pop(manifest_path, None)
                bundle.artifacts[manifest_path] = self.renderer._manifest(source, conversion, analysis, provider_result, bundle.original_path, bundle)
                pre_seal_validation = self._validate_bundle(bundle, source, catalog)
                if pre_seal_validation.errors:
                    raise PipelineError(
                        "SkillValidator failed before version seal: "
                        + "; ".join(f"{item.code}: {item.message}" for item in pre_seal_validation.errors)
                    )
                original_paths = {source.source_id: bundle.original_path.as_posix()}
                for item in skills:
                    version_root = Path(item.version_path).parent
                    skill_text = bundle.artifacts[version_root / "SKILL.md"]
                    evidence_text = bundle.artifacts[version_root / "references/evidence.json"]
                    version_text = bundle.artifacts[version_root / "VERSION.json"]
                    registry.seal_version(
                        item.name, item.version, sha256_text(skill_text), sha256_text(evidence_text),
                        original_paths, sha256_text(version_text),
                    )
                new_proposals = synthesis.propose_for_registry(registry)
                for proposal in new_proposals:
                    bundle.artifacts[synthesis.proposal_json_path(proposal["proposal_id"])] = json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                    bundle.artifacts[synthesis.proposal_md_path(proposal["proposal_id"])] = synthesis.proposal_markdown(proposal)
                next_revision = int(tx.state.get("revision", 0)) + 1
                registry.data["revision"] = next_revision
                graph.data["revision"] = next_revision
                bundle.artifacts.update({
                    REGISTRY_JSON: registry.to_json(), REGISTRY_MD: registry.to_markdown(),
                    GRAPH_JSON: graph.to_json(), GRAPH_MD: graph.to_markdown(),
                    PROPOSALS_JSON: synthesis.to_json(),
                    SOURCE_INDEX: self._source_index(source, conversion, analysis.title, edition_id, source_root),
                })
                prior_ledger_path = self.vault / ASSET_LEDGER_JSON
                prior_ledger_hash = sha256_file(prior_ledger_path) if prior_ledger_path.is_file() else None
                ledger.add_bundle(
                    bundle, source, conversion, analysis.title, next_revision, prior_ledger_hash,
                )
                bundle.artifacts[ASSET_LEDGER_JSON] = ledger.to_json()
                validation = self._validate_bundle(bundle, source, catalog)
                validation.issues[:0] = structural.issues
                if validation.errors:
                    raise PipelineError("生成结果未通过 V3 质量门：" + "; ".join(f"{item.code}: {item.message}" for item in validation.errors))
                planned = self._preflight(bundle)
                if dry_run:
                    return self._result(source, conversion, analysis, provider_result, bundle, "dry-run", [], planned, validation, graph, new_proposals, chunk_run, knowledge_synthesis, book_quality, conversion_quality)
                for relative, content in bundle.binary_artifacts.items():
                    tx.stage_bytes(relative, content)
                for relative, content in bundle.artifacts.items():
                    tx.stage_text(relative, content)
                committed = tx.commit({"source_id": source.source_id, "conversion_id": conversion.conversion_id, "skills": [item.name for item in skills]})
                result = self._result(source, conversion, analysis, provider_result, bundle, "completed", list(committed.written_paths), planned, validation, graph, new_proposals, chunk_run, knowledge_synthesis, book_quality, conversion_quality)
                result.git_status = self._git_status([*bundle.artifacts, *bundle.binary_artifacts])
                return result

    def inspect(self, source_ref: str, title: str | None = None) -> InspectionResult:
        with tempfile.TemporaryDirectory(prefix="knowledge-engine-inspect-v3-") as temporary:
            source = SourceIngester(
                Path(temporary), self.pdf_wrapper, url_allowlist=self.url_allowlist,
                azw3_command=self.azw3_command,
            ).ingest(source_ref)
            bind_source_identity(source)
            conversion_quality = self.conversion_quality_gate.validate(source)
            if not conversion_quality.passed:
                raise PipelineError("CONVERSION_QUALITY_BLOCKED: " + "; ".join(
                    item.message for item in conversion_quality.issues if item.severity == "blocker"
                ))
            normalized_document = self.structure_normalizer.normalize(source.text, source.source_kind)
            source.text = normalized_document.text
            conversion = conversion_identity(source, source.text)
            analysis = self.analyzer.analyze(source.text, title)
            catalog = EvidenceCatalog.from_text(
                analysis.title, source.source_id, source.text, conversion.conversion_id,
                conversion_locator_for=normalized_document.conversion_locator_for,
            )
            provider_result = self.provider.propose(analysis, catalog)
            validation = ValidationReport()
            for candidate in provider_result.skills:
                validation.issues.extend(self.structure_gate.validate(candidate).issues)
            validation.issues.extend(self.evidence_gate.validate(provider_result, catalog).issues)
            for candidate in provider_result.skills:
                validation.issues.extend(self.semantic_gate.assess_skill(candidate, catalog).report.issues)
                validation.issues.extend(self.executability_gate.validate(candidate).issues)
            return InspectionResult(source, conversion, analysis, catalog, provider_result, validation)

    def _archive_blocked(self, source: IngestedSource, conversion: ConversionIdentity, analysis: Analysis, reason: str, edition_id: str | None, dry_run: bool, validation: ValidationReport, chunk_run: ChunkRunResult, book_quality: BookQualityAssessment | None = None, conversion_quality: ConversionQualityAttestation | None = None) -> DistillationResult:
        # V3.2 fail-closed: blocked source/conversion bundles are not knowledge assets.
        # Operational run diagnostics remain under .knowledge-engine/runs only.
        source_root = source_directory(analysis.title, source.source_id, edition_id)
        return DistillationResult(
            analysis.title, source.source_id, conversion.conversion_id,
            "blocked-dry-run" if dry_run else "blocked", "blocked", self.vault,
            self.vault / source_root, written_paths=[], planned_paths=[], validation=validation,
            warnings=[*source.warnings, reason], chunk_run=self._chunk_dict(chunk_run),
            concept_gate={} if book_quality is None else book_quality.as_dict(),
            conversion_quality={} if conversion_quality is None else conversion_quality.as_dict(),
            conversion_provenance=dict(source.converter_metadata),
        )

    def _run_chunks_v3_legacy(self, source: IngestedSource, conversion: ConversionIdentity) -> ChunkRunResult:
        chunks = self.chunker.split(source.text, conversion.conversion_id)
        runner = ResumableChunkRunner(self.vault / ".knowledge-engine" / "runs")

        def extract(chunk):  # type: ignore[no-untyped-def]
            analysis = self.analyzer.analyze(chunk.text, chunk.chapter)
            return {
                "chunk_id": chunk.chunk_id, "chapter": chunk.chapter,
                "concepts": [{"title": item.title, "statement": item.statement} for item in analysis.concepts],
                "methods": [{"title": item.title, "steps": list(item.steps)} for item in analysis.methods],
                "cases": [{"title": item.title, "statement": item.statement} for item in analysis.cases],
            }

        provenance = getattr(self.provider, "name", self.provider.__class__.__name__)
        return runner.run(
            chunks, conversion.conversion_id, str(provenance), "rule-or-external",
            {}, "distillation-prompt-v3", "candidate-schema-v3", extract,
        )

    def _run_chunks(
        self,
        source: IngestedSource,
        conversion: ConversionIdentity,
        title: str,
        catalog: EvidenceCatalog,
    ) -> tuple[ChunkRunResult, ProviderResult]:
        chunks = self.chunker.split(source.text, conversion.conversion_id)
        runner = ResumableChunkRunner(self.vault / ".knowledge-engine" / "runs")

        def extract(chunk):  # type: ignore[no-untyped-def]
            local_analysis = self.analyzer.analyze(chunk.text, chunk.chapter or title)
            local_catalog = catalog.subset_for_range(
                chunk.start_line, chunk.end_line, chunk.owned_start_line,
            )
            mapped = self.provider.propose(local_analysis, local_catalog)
            return ProviderMapReducer.portable(mapped, local_catalog)

        provider_name = str(getattr(self.provider, "name", self.provider.__class__.__name__))
        transport = getattr(self.provider, "transport", None)
        if transport is None and hasattr(self.provider, "llm"):
            transport = getattr(self.provider.llm, "transport", None)
        model = str(getattr(transport, "model", "rule"))
        config = dict(getattr(transport, "config", None) or {})
        run = runner.run(
            chunks, conversion.conversion_id, provider_name, model, config,
            "distillation-map-prompt-v3.2.1-index-selection", "portable-provider-map-v3", extract,
        )
        if run.status != "completed":
            return run, ProviderResult(provider="map-reduce-incomplete")
        reduced, binding_hit, binding_key = ProviderMapReducer.reduce(
            self.vault / ".knowledge-engine" / "runs",
            conversion.conversion_id,
            run.chunks,
            run.results,
            run.extraction_hashes,
            catalog,
        )
        run = ChunkRunResult(
            run_id=run.run_id,
            status=run.status,
            chunks=run.chunks,
            processed=run.processed,
            cache_hits=run.cache_hits,
            failed_chunk=run.failed_chunk,
            elapsed_seconds=run.elapsed_seconds,
            estimated_tokens=run.estimated_tokens,
            warnings=run.warnings,
            results=run.results,
            extraction_hashes=run.extraction_hashes,
            binding_cache_hit=binding_hit,
            binding_cache_key=binding_key,
        )
        return run, reduced

    def _source_index(self, source: IngestedSource, conversion: ConversionIdentity, title: str, edition_id: str | None, source_root: Path) -> str:
        path = self.vault / SOURCE_INDEX
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise PipelineError(f"Source index 无法读取：{exc}") from exc
        else:
            data = {"schema_version": 4, "engine_version": "3.3.0", "sources": {}, "titles": {}}
        record = data["sources"].setdefault(source.source_id, {
            "source_id": source.source_id, "title": title, "aliases": [], "editions": [],
            "source_root": source_root.as_posix(), "original_name": source.original_name,
            "original_hash": source.original_hash, "format": source.source_kind, "conversions": [],
        })
        if title not in record["aliases"]:
            record["aliases"].append(title)
        edition = edition_id or "unspecified"
        if edition not in record["editions"]:
            record["editions"].append(edition)
        if conversion.conversion_id not in record["conversions"]:
            record["conversions"].append(conversion.conversion_id)
        key = title.casefold()
        if source.source_id not in data["titles"].setdefault(key, []):
            data["titles"][key].append(source.source_id)
        data["updated_at"] = now_iso()
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def _validate_existing_vault(self) -> None:
        if not self.vault.is_dir():
            raise PipelineError(f"Vault 不存在；V3 拒绝创建第二套知识库：{self.vault}")
        missing = [str(path) for path in (self.vault / "05_Knowledge", self.vault / "06_Sources") if not path.is_dir()]
        if missing:
            raise PipelineError("目标目录不是现有 Obsidian Knowledge OS；缺少：" + ", ".join(missing))

    def _validate_bundle(self, bundle: RenderedBundle, source: IngestedSource, catalog: EvidenceCatalog) -> ValidationReport:
        report = ValidationReport()
        for path, text in bundle.artifacts.items():
            if path.name == "SKILL.md":
                report.issues.extend(self.validator.validate_skill_text(text, str(path), source.text, catalog).issues)
        return report

    def _preflight(self, bundle: RenderedBundle) -> list[Path]:
        planned: list[Path] = []
        root_skill_paths = set(bundle.skill_paths)
        for relative, content in {**bundle.artifacts, **bundle.binary_artifacts}.items():
            target = (self.vault / relative).resolve(strict=False)
            try:
                target.relative_to(self.vault)
            except ValueError as exc:
                raise PipelineError(f"输出路径越出 Vault：{relative}") from exc
            planned.append(target)
            if not target.is_file():
                continue
            incoming = content if isinstance(content, bytes) else content.encode("utf-8")
            if target.read_bytes() == incoming:
                continue
            mutable = relative in self.MANAGED_MUTABLE or relative in root_skill_paths or any(relative == path.parent / "agents/openai.yaml" or relative == path.parent / "references/evidence.json" for path in root_skill_paths)
            if not mutable:
                raise PipelineError(f"不可变资产发生覆盖冲突：{target}")
        return planned

    def _result(self, source: IngestedSource, conversion: ConversionIdentity, analysis: Analysis, provider: ProviderResult, bundle: RenderedBundle, status: str, written: list[Path], planned: list[Path], validation: ValidationReport, graph: ConceptGraph, proposals: list[dict[str, Any]], chunk_run: ChunkRunResult, knowledge_synthesis: KnowledgeSynthesisResult | None = None, book_quality: BookQualityAssessment | None = None, conversion_quality: ConversionQualityAttestation | None = None) -> DistillationResult:
        return DistillationResult(
            analysis.title, source.source_id, conversion.conversion_id, status, provider.provider,
            self.vault, self.vault / bundle.source_root, written, planned,
            [self.vault / path for path in bundle.skill_paths], [self.vault / path for path in bundle.knowledge_paths],
            validation, warnings=[*source.warnings, *provider.warnings],
            graph_nodes=len(graph.nodes), graph_edges=len(graph.edges),
            synthesis_proposals=[item["proposal_id"] for item in proposals], chunk_run=self._chunk_dict(chunk_run),
            method_clusters=[item.id for item in (knowledge_synthesis.method_clusters if knowledge_synthesis else [])],
            concept_gate={} if book_quality is None else book_quality.as_dict(),
            conversion_quality={} if conversion_quality is None else conversion_quality.as_dict(),
            conversion_provenance=dict(source.converter_metadata),
        )

    def _write_conversion_quality_diagnostic(
        self,
        source: IngestedSource,
        attestation: ConversionQualityAttestation,
    ) -> Path:
        path = (
            self.vault / ".knowledge-engine/runs/conversion-quality"
            / f"{source.source_id[:16]}-{attestation.normalized_output_sha256[:16]}.json"
        )
        payload = {
            **attestation.as_dict(),
            "result": "CONVERSION_QUALITY_BLOCKED",
            "formal_writes": {
                "source": 0,
                "graph": 0,
                "registry": 0,
                "skill": 0,
                "asset_ledger": 0,
            },
            "llm_calls": 0,
        }
        atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return path

    @staticmethod
    def _chunk_dict(result: ChunkRunResult) -> dict[str, Any]:
        return {
            "run_id": result.run_id, "status": result.status, "chunk_count": len(result.chunks),
            "processed": list(result.processed), "cache_hits": list(result.cache_hits),
            "failed_chunk": result.failed_chunk, "elapsed_seconds": round(result.elapsed_seconds, 4),
            "estimated_tokens": result.estimated_tokens, "warnings": list(result.warnings),
            "extraction_hashes": result.extraction_hashes,
            "binding_cache_hit": result.binding_cache_hit,
            "binding_cache_key": result.binding_cache_key,
        }

    def _git_status(self, relative_paths: list[Path]) -> list[str]:
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.vault), "status", "--short", "--", *[str(path) for path in relative_paths]],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            return ["Git status unavailable; no commit was created."]
        if completed.returncode != 0:
            return ["Target vault is not a readable Git worktree; no commit was created."]
        return [line for line in completed.stdout.splitlines() if line.strip()]
