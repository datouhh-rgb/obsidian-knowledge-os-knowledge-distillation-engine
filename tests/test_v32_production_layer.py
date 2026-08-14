from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from conftest import FixtureEvidenceBoundProvider, make_production_pipeline
from knowledge_engine.analyzer import BookAnalyzer
from knowledge_engine.chunking import ChapterChunker
from knowledge_engine.concepts import ConceptCanonicalizer, ConceptQualityGate
from knowledge_engine.evidence import EvidenceCatalog
from knowledge_engine.evidence_selector import EvidenceScope, EvidenceSelectionError, EvidenceSelector
from knowledge_engine.graph import ConceptGraph, GraphDryRunResult
from knowledge_engine.identity import bind_source_identity, build_source_identity, conversion_identity
from knowledge_engine.ingest import SourceIngester
from knowledge_engine.models import (
    Analysis,
    ConceptCandidate,
    GroundedStatement,
    IngestedSource,
    ProviderResult,
    QualityScore,
    SkillCandidate,
)
from knowledge_engine.production_gates import SkillProductionGate
from knowledge_engine.pipeline import DistillationPipeline
from knowledge_engine.providers import AnalyzerProviderError, LLMAnalyzerProvider, RuleAnalyzerProvider
from knowledge_engine.registry import SkillRegistry
from knowledge_engine.structure import RealStructureNormalizer
from knowledge_engine.utils import canonical_json, sha256_file


FIXTURES = Path(__file__).parent / "fixtures" / "real_failures"


class PayloadTransport:
    name = "fixture"
    model = "fixture-model"
    config = {"temperature": 0}

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def complete(self, request):  # type: ignore[no-untyped-def]
        return self.payload


def _catalog(text: str = "# Book\n\nalpha evidence.\n\nbeta evidence.\n") -> EvidenceCatalog:
    return EvidenceCatalog.from_text("Book", "a" * 64, text, "b" * 64)


def _candidate(evidence_ids: tuple[str, ...], provider: str = "llm:fixture") -> SkillCandidate:
    first = GroundedStatement(
        'When the source procedure applies, check "alpha evidence" and record the result; stop if unverified.',
        evidence_ids,
    )
    second = GroundedStatement(
        'When verification is required, compare "beta evidence" and return the status; stop if unsupported.',
        evidence_ids,
    )
    policy = lambda text: GroundedStatement(text, (), "engine_policy")
    return SkillCandidate(
        "Evidence Procedure", policy("Evidence-bound procedure."),
        (policy("Use for an explicit verification request."),),
        (policy("Provide the source object and constraints."),),
        (policy("Check task fit."), policy("Check input completeness.")),
        (first, second), policy("Return decisions, actions, and Evidence locators."),
        (policy("Do not exceed the source."),),
        (policy("Do not use without the source or required inputs."),),
        provider, 0.9,
    )


def _passing_score() -> QualityScore:
    return QualityScore(100, 100, 100, None, None, 0, 100, None, None, 100, 92, "pass", ())


def test_source_identity_contract(tmp_path: Path) -> None:
    path = tmp_path / "book.md"
    path.write_text("# Book\n\nEvidence.\n", encoding="utf-8")
    source = SourceIngester(tmp_path / "work").ingest(str(path))
    bind_source_identity(source, "first-edition")
    conversion = conversion_identity(source)
    identity = build_source_identity(source, conversion, "first-edition", "2026-01-01T00:00:00+00:00")
    assert identity.original_hash == sha256_file(path)
    assert identity.source_id == source.source_id != identity.original_hash
    assert identity.format == "markdown"
    assert identity.conversion_hash == conversion.normalized_text_sha256
    assert identity.as_dict()["edition_id"] == "first-edition"


def test_conversion_identity(tmp_path: Path) -> None:
    path = tmp_path / "book.md"; path.write_text("# Book\n\nEvidence.\n", encoding="utf-8")
    raw_hash = sha256_file(path)
    one = IngestedSource(str(path), path, path.name, "text/markdown", "markdown", raw_hash, path.read_text(encoding="utf-8"), {"converter": "identity", "version": "1", "config": {"mode": "a"}})
    two = IngestedSource(str(path), path, path.name, "text/markdown", "markdown", raw_hash, path.read_text(encoding="utf-8"), {"converter": "identity", "version": "1", "config": {"mode": "b"}})
    assert conversion_identity(one).conversion_id != conversion_identity(two).conversion_id
    assert conversion_identity(one).conversion_hash == conversion_identity(two).conversion_hash


def test_llm_fake_evidence_block() -> None:
    catalog = _catalog(); fake = "ev-" + "f" * 64
    payload = {
        "concepts": [{"title": "Evidence", "definition": {"text": "alpha evidence.", "evidence_ids": [fake]}}],
        "relations": [], "skills": [],
    }
    with pytest.raises(AnalyzerProviderError, match="forbidden Evidence field"):
        LLMAnalyzerProvider(PayloadTransport(payload)).propose(Analysis("Book"), catalog)


def test_llm_cannot_create_fake_evidence() -> None:
    test_llm_fake_evidence_block()


def test_fake_evidence_pipeline_writes_no_knowledge(vault: Path, source_factory) -> None:
    fake = "ev-" + "f" * 64
    payload = {
        "concepts": [{"title": "Evidence", "definition": {"text": "invented", "evidence_ids": [fake]}}],
        "relations": [], "skills": [],
    }
    before = {path.relative_to(vault).as_posix() for path in vault.rglob("*") if path.is_file()}
    result = DistillationPipeline(vault, provider=LLMAnalyzerProvider(PayloadTransport(payload))).distill(
        str(source_factory()), title="Fake Evidence Book",
    )
    assert result.status == "blocked"
    assert result.chunk_run and result.chunk_run["status"] == "blocked"
    after = {path.relative_to(vault).as_posix() for path in vault.rglob("*") if path.is_file()}
    assert not any(path.startswith("05_Knowledge/") or path.startswith("06_Sources/") for path in after - before)


def test_evidence_scope_validation() -> None:
    catalog = _catalog()
    ids = sorted(catalog.items, key=lambda item: catalog.items[item].start_line)
    outside = catalog.items[ids[-1]]
    scope = EvidenceScope(catalog.source_id, catalog.conversion_id, 1, outside.start_line - 1)
    candidate_index = next(
        item["index"] for item in EvidenceSelector(catalog, scope).prompt_candidates()
        if item["locator"] == outside.locator
    )
    with pytest.raises(EvidenceSelectionError, match="outside the current chunk"):
        EvidenceSelector(catalog, scope).select([candidate_index])


def test_evidence_must_belong_to_chunk() -> None:
    test_evidence_scope_validation()


def test_evidence_source_scope() -> None:
    catalog = _catalog()
    with pytest.raises(EvidenceSelectionError, match="scope does not match"):
        EvidenceSelector(catalog, EvidenceScope("c" * 64, catalog.conversion_id))


def test_concept_alias_merge() -> None:
    candidates = [
        ConceptCandidate("第二节 自控力", GroundedStatement("第二节 自控力：我们生而为人就是为了成为思维舵", ("ev-a",)), "rule", 0.8),
        ConceptCandidate("第二节 自控力：我们生而为人就是为了成为思维", GroundedStatement("第二节 自控力：我们生而为人就是为了成为思维", ("ev-b",)), "rule", 0.8),
    ]
    result = ConceptCanonicalizer().canonicalize(candidates, "s" * 64)
    assert len(result.concepts) == 1 and not result.conflicts
    assert result.concepts[0].canonical_title == "第二节 自控力"
    assert set(result.concepts[0].evidence_ids) == {"ev-a", "ev-b"}
    assert len(result.concepts[0].aliases) == 2


def test_concept_conflict_detection() -> None:
    candidates = [
        ConceptCandidate("现金原则", GroundedStatement("必须保留全部现金。", ("ev-a",)), "llm", 0.9),
        ConceptCandidate("现金原则", GroundedStatement("不得保留任何现金。", ("ev-b",)), "llm", 0.9),
    ]
    result = ConceptCanonicalizer().canonicalize(candidates, "s" * 64)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].status == "unresolved"


def test_chunk_order_independence() -> None:
    catalog = _catalog()
    ids = tuple(catalog.items)
    concepts = [
        ConceptCandidate("Alpha", GroundedStatement("alpha evidence.", (ids[0],)), "llm", 0.8),
        ConceptCandidate("Beta", GroundedStatement("beta evidence.", (ids[-1],)), "llm", 0.8),
    ]
    one = ConceptCanonicalizer().canonicalize(concepts, catalog.source_id)
    two = ConceptCanonicalizer().canonicalize(reversed(concepts), catalog.source_id)
    provider_one = ProviderResult("llm", concepts=one.candidates)
    provider_two = ProviderResult("llm", concepts=two.candidates)
    graph_one = ConceptGraph.empty().dry_run_distillation("Book", catalog.source_id, catalog.conversion_id, Analysis("Book"), catalog, provider_one)
    graph_two = ConceptGraph.empty().dry_run_distillation("Book", catalog.source_id, catalog.conversion_id, Analysis("Book"), catalog, provider_two)
    assert graph_one.passed and graph_two.passed
    assert graph_one.node_ids == graph_two.node_ids
    assert graph_one.edge_ids == graph_two.edge_ids
    assert graph_one.deterministic_digest == graph_two.deterministic_digest


def test_pdf_structure_normalization() -> None:
    text = (
        "Book Header\n1\n第1章 认知\fBook Header\n2\n觉醒\nThis is hy-\nphenated text\nCopyright\f"
        "Book Header\n3\n## 第二章 方法\n正文第一行\n继续正文。\nCopyright\n"
    )
    document = RealStructureNormalizer().normalize(text, "pdf")
    assert "Book Header" not in document.text
    assert "Copyright" not in document.text
    assert "## 第1章 认知觉醒" in document.text
    assert "hyphenated text" in document.text
    assert document.locator_map
    assert document.conversion_locator_for(1, 1).startswith("lines:")


def test_azw3_source_identity(tmp_path: Path) -> None:
    azw3 = tmp_path / "book.azw3"; azw3.write_bytes(b"immutable-azw3-fixture")
    converter = tmp_path / "converter.py"
    converter.write_text(
        """from pathlib import Path
import sys
output = Path(sys.argv[2])
(output / 'META-INF').mkdir(parents=True)
(output / 'OEBPS/Text').mkdir(parents=True)
(output / 'META-INF/container.xml').write_text(
    \"<container><rootfiles><rootfile full-path='OEBPS/content.opf'/></rootfiles></container>\", encoding='utf-8')
(output / 'OEBPS/content.opf').write_text(
    \"<package><manifest><item id='c' href='Text/c.xhtml' media-type='application/xhtml+xml'/></manifest><spine><itemref idref='c'/></spine></package>\", encoding='utf-8')
(output / 'OEBPS/Text/c.xhtml').write_text(
    \"<?xml version='1.0' encoding='utf-8'?><html><body><h1>Book</h1><p>Converted evidence.</p></body></html>\", encoding='utf-8')
print('KindleUnpack v0.83')
""",
        encoding="utf-8",
    )
    source = SourceIngester(
        tmp_path / "work", azw3_command=(sys.executable, str(converter), "{input}", "{output_dir}"),
    ).ingest(str(azw3))
    bind_source_identity(source, "azw3-edition")
    identity = build_source_identity(source, conversion_identity(source), "azw3-edition", "2026-01-01T00:00:00+00:00")
    assert identity.original_hash == hashlib.sha256(azw3.read_bytes()).hexdigest()
    assert identity.format == "azw3"
    assert identity.converter == "KindleUnpack canonical EPUB spine adapter"
    assert source.local_path == azw3.resolve()


def test_fragment_concept_filter() -> None:
    gate = ConceptQualityGate()
    candidates = [
        ConceptCandidate("这就", GroundedStatement("这就", ("ev-a",)), "llm"),
        ConceptCandidate("熵", GroundedStatement("熵用于描述系统状态的不确定程度。", ("ev-b",)), "llm"),
        ConceptCandidate("复利", GroundedStatement("复利把先前收益纳入后续收益计算。", ("ev-c",)), "llm"),
    ]
    result = gate.validate(candidates)
    assert {item.title for item in result.rejected} == {"这就"}
    assert {item.title for item in result.accepted} == {"熵", "复利"}


def test_rule_analyzer_not_production() -> None:
    text = "# Book\n\n## 方法\n\n1. 保存原件。\n2. 核对哈希。\n"
    analysis = BookAnalyzer().analyze(text, "Book")
    result = RuleAnalyzerProvider().propose(analysis, EvidenceCatalog.from_text("Book", "a" * 64, text, "b" * 64))
    assert not result.skills
    assert result.provenance["skill_production"] is False


def test_skill_quality_gate() -> None:
    catalog = _catalog(); candidate = _candidate(tuple(catalog.items))
    dry_run = GraphDryRunResult(True, ConceptGraph.empty(), (), (), (), "digest")
    passed = SkillProductionGate().validate(
        candidate, catalog, concepts_stable=True, graph_dry_run=dry_run, quality_score=_passing_score(),
    )
    assert passed.passed
    blocked_score = QualityScore(100, 100, 100, None, None, 90, 100, None, None, 100, 80, "blocked", ())
    blocked = SkillProductionGate().validate(
        candidate, catalog, concepts_stable=True, graph_dry_run=dry_run, quality_score=blocked_score,
    )
    assert any(item.code == "skill-quality-blocked" for item in blocked.errors)


def test_graph_dry_run_before_commit(vault: Path, source_factory, monkeypatch) -> None:
    state = {"dry_run": False, "registry": False}
    original_dry_run = ConceptGraph.dry_run_distillation
    original_create = SkillRegistry.create_source_candidate

    def dry_run(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        state["dry_run"] = True
        return original_dry_run(self, *args, **kwargs)

    def create(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        assert state["dry_run"]
        state["registry"] = True
        return original_create(self, *args, **kwargs)

    monkeypatch.setattr(ConceptGraph, "dry_run_distillation", dry_run)
    monkeypatch.setattr(SkillRegistry, "create_source_candidate", create)
    result = make_production_pipeline(vault).distill(str(source_factory()), title="Book")
    assert result.status == "completed"
    assert state == {"dry_run": True, "registry": True}


def test_real_book_regression_case_a() -> None:
    fixture_path = FIXTURES / "case_a" / "fixture.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    run_path = FIXTURES / "case_a" / "RUN.original.json"
    binding_path = FIXTURES / "case_a" / "binding-cache.original.json"
    assert sha256_file(run_path) == fixture["original_run_sha256"]
    assert sha256_file(binding_path) == fixture["original_binding_cache_sha256"]
    candidates = [
        ConceptCandidate(
            item["title"], GroundedStatement(item["definition"], (item["evidence_id"],)), "rule", 0.8,
        ) for item in fixture["candidates"]
    ]
    canonical = ConceptCanonicalizer().canonicalize(candidates, fixture["source_id"])
    assert len(canonical.concepts) == 1 and not canonical.conflicts
    catalog = EvidenceCatalog.from_text("认知觉醒", fixture["source_id"], "# 认知觉醒\n\n自控力是思维的舵。\n", fixture["conversion_id"])
    # Use catalog IDs in the graph while the canonicalizer regression above preserves the historical IDs.
    concept = ConceptCandidate(canonical.concepts[0].canonical_title, GroundedStatement("自控力是思维的舵。", tuple(catalog.items)), "rule", 0.8)
    result = ConceptGraph.empty().dry_run_distillation(
        "认知觉醒", fixture["source_id"], fixture["conversion_id"], Analysis("认知觉醒"), catalog,
        ProviderResult("rule", concepts=[concept]),
    )
    assert result.passed
    assert len(result.edge_ids) == len(set(result.edge_ids))


def test_real_book_regression_case_b(tmp_path: Path) -> None:
    fixture = json.loads((FIXTURES / "case_b" / "fixture.json").read_text(encoding="utf-8"))
    run_path = FIXTURES / "case_b" / "RUN.original.json"
    assert sha256_file(run_path) == fixture["original_run_sha256"]
    run = json.loads(run_path.read_text(encoding="utf-8"))
    assert run["failed_chunk"] == fixture["failed_chunk"]
    assert fixture["error"] in run["warnings"]
    catalog = _catalog(); fake = fixture["error"].rsplit(" ", 1)[-1]
    payload = {"concepts": [{"title": "风险", "definition": {"text": "alpha evidence.", "evidence_ids": [fake]}}], "relations": [], "skills": []}
    with pytest.raises(AnalyzerProviderError, match="forbidden Evidence field"):
        LLMAnalyzerProvider(PayloadTransport(payload)).propose(Analysis("股票大作手回忆录"), catalog)
    original = tmp_path / "book.azw3"; original.write_bytes(b"fixture-original")
    source = IngestedSource(str(original), original, original.name, "application/octet-stream", "azw3", fixture["original_azw3_hash"], "# converted\n", {"converter": "fixture", "version": "1", "config": {}})
    bind_source_identity(source)
    assert source.source_id == fixture["original_azw3_hash"]
    assert source.source_id != fixture["source_id_v31_converted_markdown"]
