from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from .evidence import EvidenceCatalog
from .evidence_selector import EvidenceScope, EvidenceSelectionError, EvidenceSelector
from .models import (
    ConceptCandidate,
    ConceptRelationCandidate,
    GroundedStatement,
    ProviderResult,
    SkillCandidate,
)
from .utils import atomic_write, canonical_json, normalize_text, now_iso, sha256_text


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    chapter: str
    start_char: int
    end_char: int
    start_line: int
    end_line: int
    owned_start_line: int
    text: str
    overlap_before: int
    text_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "chapter": self.chapter,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "owned_start_line": self.owned_start_line,
            "overlap_before": self.overlap_before,
            "normalized_content_sha256": self.text_sha256,
            "character_count": len(self.text),
            "estimated_tokens": max(1, len(self.text) // 4),
        }


@dataclass(frozen=True)
class ChunkBudget:
    max_chunk_tokens: int = 8_000
    max_total_tokens: int = 2_000_000
    concurrency: int = 1
    retries: int = 2
    timeout_seconds: int = 240
    max_estimated_cost: float = 100.0
    estimated_cost_per_million_tokens: float = 5.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_chunk_tokens": self.max_chunk_tokens,
            "max_total_tokens": self.max_total_tokens,
            "concurrency": self.concurrency,
            "retries": self.retries,
            "timeout_seconds": self.timeout_seconds,
            "max_estimated_cost": self.max_estimated_cost,
            "estimated_cost_per_million_tokens": self.estimated_cost_per_million_tokens,
        }


@dataclass(frozen=True)
class ChunkRunResult:
    run_id: str
    status: str
    chunks: tuple[DocumentChunk, ...]
    processed: tuple[str, ...]
    cache_hits: tuple[str, ...]
    failed_chunk: str | None
    elapsed_seconds: float
    estimated_tokens: int
    warnings: tuple[str, ...]
    results: dict[str, dict[str, Any]]
    extraction_hashes: dict[str, str]
    binding_cache_hit: bool = False
    binding_cache_key: str | None = None


class ChapterChunker:
    VERSION = "chapter-chunker-v2"

    def __init__(self, max_chars: int = 12_000, overlap: int = 300, max_total_chars: int = 20_000_000) -> None:
        if max_chars < 500 or overlap < 0 or overlap >= max_chars:
            raise ValueError("Invalid chunking parameters.")
        self.max_chars = max_chars
        self.overlap = overlap
        self.max_total_chars = max_total_chars

    def split(self, text: str, conversion_id: str) -> list[DocumentChunk]:
        normalized = normalize_text(text)
        if len(normalized) > self.max_total_chars:
            raise ValueError(f"Document exceeds the {self.max_total_chars} character budget.")
        boundaries = self._chapters(normalized)
        chunks: list[DocumentChunk] = []
        chapter_sequence: dict[str, int] = {}
        for chapter, chapter_start, chapter_end in boundaries:
            cursor = chapter_start
            while cursor < chapter_end:
                raw_end = min(chapter_end, cursor + self.max_chars)
                end = self._natural_break(normalized, cursor, raw_end, chapter_end)
                start = cursor if not chunks or chunks[-1].chapter != chapter else max(chapter_start, cursor - self.overlap)
                chunk_text = normalize_text(normalized[start:end])
                text_hash = sha256_text(chunk_text)
                sequence = chapter_sequence.get(chapter, 0)
                chapter_sequence[chapter] = sequence + 1
                # chunk_id identifies this binding location; extraction cache does not use it.
                payload = f"{conversion_id}\0{chapter}\0{sequence}\0{start}\0{end}\0{text_hash}\0{self.VERSION}"
                start_line = normalized.count("\n", 0, start) + 1
                owned_start_line = normalized.count("\n", 0, cursor) + 1
                end_line = start_line + max(0, len(chunk_text.splitlines()) - 1)
                chunks.append(DocumentChunk(
                    chunk_id="chunk-" + sha256_text(payload)[:24],
                    chapter=chapter,
                    start_char=start,
                    end_char=end,
                    start_line=start_line,
                    end_line=end_line,
                    owned_start_line=owned_start_line,
                    text=chunk_text,
                    overlap_before=max(0, cursor - start),
                    text_sha256=text_hash,
                ))
                if end <= cursor:
                    raise RuntimeError("Chunker did not advance.")
                cursor = end
        return chunks

    @staticmethod
    def _chapters(text: str) -> list[tuple[str, int, int]]:
        headings = list(re.finditer(r"(?m)^#{1,3}\s+(.+?)\s*$", text))
        if not headings:
            return [("document", 0, len(text))]
        chapters: list[tuple[str, int, int]] = []
        if headings[0].start() > 0:
            chapters.append(("preamble", 0, headings[0].start()))
        for index, match in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            chapters.append((match.group(1).strip(), match.start(), end))
        return [item for item in chapters if item[2] > item[1]]

    @staticmethod
    def _natural_break(text: str, start: int, raw_end: int, chapter_end: int) -> int:
        if raw_end >= chapter_end:
            return chapter_end
        window_start = max(start, raw_end - 1000)
        window = text[window_start:raw_end]
        for marker in ("\n\n", "。", ". ", "\n"):
            offset = window.rfind(marker)
            if offset >= 0:
                return max(start + 1, window_start + offset + len(marker))
        return raw_end


class ResumableChunkRunner:
    """Content-addressed extraction cache independent of source conversion identity."""

    SCHEMA_VERSION = "extraction-cache-v2"

    def __init__(self, state_dir: Path, budget: ChunkBudget | None = None) -> None:
        self.state_dir = state_dir
        self.extraction_cache = state_dir / "extraction-cache"
        self.binding_runs = state_dir / "binding-runs"
        self.extraction_cache.mkdir(parents=True, exist_ok=True)
        self.binding_runs.mkdir(parents=True, exist_ok=True)
        self.budget = budget or ChunkBudget()

    def run(
        self,
        chunks: list[DocumentChunk],
        conversion_id: str,
        provider: str,
        model: str,
        config: dict[str, Any],
        prompt_version: str,
        schema_version: str,
        extractor: Callable[[DocumentChunk], dict[str, Any]],
        fail_after: int | None = None,
    ) -> ChunkRunResult:
        estimated_tokens = sum(max(1, len(item.text) // 4) for item in chunks)
        if any(max(1, len(item.text) // 4) > self.budget.max_chunk_tokens for item in chunks):
            raise ValueError("A chunk exceeds max_chunk_tokens.")
        if estimated_tokens > self.budget.max_total_tokens:
            raise ValueError("Document exceeds max_total_tokens.")
        estimated_cost = estimated_tokens / 1_000_000 * self.budget.estimated_cost_per_million_tokens
        if estimated_cost > self.budget.max_estimated_cost:
            raise ValueError("Document exceeds max_estimated_cost.")
        provider_key = {
            "provider": provider,
            "model": model,
            "config": config,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "chunker": ChapterChunker.VERSION,
        }
        binding_key_payload = {
            "conversion_id": conversion_id,
            "provider_key": provider_key,
            "chunks": [{"chunk_id": item.chunk_id, "content": item.text_sha256} for item in chunks],
        }
        run_id = "run-" + sha256_text(canonical_json(binding_key_payload))[:24]
        run_dir = self.binding_runs / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "RUN.json"
        started = time.monotonic()
        results: dict[str, dict[str, Any]] = {}
        extraction_hashes: dict[str, str] = {}
        processed: list[str] = []
        cache_hits: list[str] = []
        failed_chunk: str | None = None
        warnings: list[str] = []
        failure: dict[str, Any] | None = None
        chunk_statuses: dict[str, str] = {}
        for chunk in chunks:
            cache_key_payload = {
                "normalized_content_sha256": chunk.text_sha256,
                **provider_key,
            }
            cache_key = sha256_text(canonical_json(cache_key_payload))
            cache_path = self.extraction_cache / f"{cache_key}.json"
            if cache_path.is_file():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached.get("cache_key") == cache_key and cached.get("status") == "completed":
                    result = cached["result"]
                    extraction_hash = sha256_text(canonical_json(result))
                    if cached.get("extraction_hash") != extraction_hash:
                        raise ValueError(f"Extraction cache hash mismatch: {cache_path}")
                    results[chunk.chunk_id] = result
                    extraction_hashes[chunk.chunk_id] = extraction_hash
                    cache_hits.append(chunk.chunk_id)
                    chunk_statuses[chunk.chunk_id] = "CACHED"
                    continue
            try:
                if fail_after is not None and len(processed) >= fail_after:
                    raise RuntimeError("fault-injection: chunk extraction")
                result = self._extract_with_policy(extractor, chunk)
                extraction_hash = sha256_text(canonical_json(result))
                cache_record = {
                    "schema_version": self.SCHEMA_VERSION,
                    "cache_key": cache_key,
                    "cache_key_fields": cache_key_payload,
                    "status": "completed",
                    "completed_at": now_iso(),
                    "extraction_hash": extraction_hash,
                    "result": result,
                }
                atomic_write(cache_path, json.dumps(cache_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                results[chunk.chunk_id] = result
                extraction_hashes[chunk.chunk_id] = extraction_hash
                processed.append(chunk.chunk_id)
                chunk_statuses[chunk.chunk_id] = "COMPLETED"
            except Exception as exc:
                failed_chunk = chunk.chunk_id
                root = exc
                while root.__cause__ is not None:
                    root = root.__cause__
                failure = {
                    "type": type(root).__name__,
                    "message": str(root),
                    "retry_attempts": self.budget.retries + 1,
                }
                warnings.append(f"{failure['type']}: {failure['message']}")
                chunk_statuses[chunk.chunk_id] = "FAILED"
                break
        complete = failed_chunk is None and len(results) == len(chunks)
        run_status = "completed" if complete else (
            "blocked" if failure and failure["type"] == "EvidenceSelectionError" else "interrupted"
        )
        manifest = {
            "schema_version": self.SCHEMA_VERSION,
            "run_id": run_id,
            "status": run_status,
            "conversion_id": conversion_id,
            "provider": provider,
            "model": model,
            "config": config,
            "prompt_version": prompt_version,
            "output_schema_version": schema_version,
            "budgets": self.budget.as_dict(),
            "estimated_tokens": estimated_tokens,
            "estimated_cost": round(estimated_cost, 6),
            "chunks": [item.as_dict() for item in chunks],
            "processed": processed,
            "cache_hits": cache_hits,
            "extraction_hashes": extraction_hashes,
            "failed_chunk": failed_chunk,
            "failed_chunk_status": "FAILED" if failed_chunk else None,
            "failure": failure,
            "chunk_statuses": chunk_statuses,
            "unprocessed": [item.chunk_id for item in chunks if item.chunk_id not in results],
            "warnings": warnings,
            "updated_at": now_iso(),
        }
        atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        # Compatibility pointer for V3.0 tooling; extraction data remains solely in
        # the global content-addressed extraction-cache directory.
        atomic_write(
            self.state_dir / run_id / "RUN.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        return ChunkRunResult(
            run_id=run_id,
            status=run_status,
            chunks=tuple(chunks),
            processed=tuple(processed),
            cache_hits=tuple(cache_hits),
            failed_chunk=failed_chunk,
            elapsed_seconds=time.monotonic() - started,
            estimated_tokens=estimated_tokens,
            warnings=tuple(warnings),
            results=results,
            extraction_hashes=extraction_hashes,
        )

    def _extract_with_policy(
        self,
        extractor: Callable[[DocumentChunk], dict[str, Any]],
        chunk: DocumentChunk,
    ) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self.budget.retries + 1):
            try:
                with ThreadPoolExecutor(max_workers=max(1, self.budget.concurrency)) as pool:
                    future = pool.submit(extractor, chunk)
                    value = future.result(timeout=self.budget.timeout_seconds)
                if not isinstance(value, dict):
                    raise TypeError("Chunk extractor must return a JSON object.")
                return value
            except FutureTimeout as exc:
                last = TimeoutError(f"Chunk extraction timed out after {self.budget.timeout_seconds}s")
            except Exception as exc:  # noqa: PERF203 - retries are policy, not a hot loop.
                last = exc
            if attempt < self.budget.retries:
                continue
        assert last is not None
        raise last


class ProviderMapReducer:
    """Portable map payload plus current-conversion Evidence rebinding and reduce."""

    VERSION = "provider-map-reducer-v3-evidence-index-selection"

    @classmethod
    def portable(cls, result: ProviderResult, local_catalog: EvidenceCatalog) -> dict[str, Any]:
        selector = EvidenceSelector(local_catalog)

        def statement(value: GroundedStatement) -> dict[str, Any]:
            indexes = selector.indexes_for_evidence_ids(value.evidence_ids) if value.evidence_ids else ()
            return {
                "text": value.text,
                "origin": value.origin,
                "selected_evidence_indexes": list(indexes),
            }

        payload = {
            "schema_version": "portable-provider-map-v3",
            "provider": result.provider,
            "provenance": result.provenance,
            "warnings": result.warnings,
            "evidence_selection_candidates": selector.portable_candidates(),
            "concepts": [{
                "title": item.title, "definition": statement(item.definition),
                "provider": item.provider, "confidence": item.confidence,
            } for item in result.concepts],
            "relations": [{
                "source_concept": item.source_concept, "target_concept": item.target_concept,
                "relation": statement(item.relation), "provider": item.provider, "confidence": item.confidence,
            } for item in result.relations],
            "skills": [{
                "title": item.title,
                "positioning": statement(item.positioning),
                "scenarios": [statement(value) for value in item.scenarios],
                "input_requirements": [statement(value) for value in item.input_requirements],
                "decision_flow": [statement(value) for value in item.decision_flow],
                "actions": [statement(value) for value in item.actions],
                "output_format": statement(item.output_format),
                "boundaries": [statement(value) for value in item.boundaries],
                "prohibited_scenarios": [statement(value) for value in item.prohibited_scenarios],
                "provider": item.provider,
                "confidence": item.confidence,
            } for item in result.skills],
        }
        cls.validate_portable(payload)
        return payload

    @classmethod
    def validate_portable(cls, payload: dict[str, Any]) -> None:
        def reject_evidence_ids(value: Any, path: str = "$") -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"evidence_id", "evidence_ids"}:
                        raise EvidenceSelectionError(
                            f"Portable provider map contains forbidden field at {path}.{key}"
                        )
                    reject_evidence_ids(item, f"{path}.{key}")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    reject_evidence_ids(item, f"{path}[{index}]")

        reject_evidence_ids(payload)
        errors = sorted(
            Draft202012Validator(cls._portable_schema()).iter_errors(payload),
            key=lambda item: list(item.path),
        )
        if errors:
            first = errors[0]
            raise EvidenceSelectionError(
                f"Portable provider map schema blocked at {first.json_path}: {first.message}"
            )

    @staticmethod
    def _portable_schema() -> dict[str, Any]:
        source_statement = {
            "type": "object",
            "additionalProperties": False,
            "required": ["text", "origin", "selected_evidence_indexes"],
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 10_000},
                "origin": {"type": "string", "enum": ["source", "engine_policy"]},
                "selected_evidence_indexes": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {"type": "integer", "minimum": 1},
                },
            },
            "allOf": [
                {
                    "if": {"properties": {"origin": {"const": "source"}}},
                    "then": {"properties": {"selected_evidence_indexes": {"minItems": 1}}},
                },
                {
                    "if": {"properties": {"origin": {"const": "engine_policy"}}},
                    "then": {"properties": {"selected_evidence_indexes": {"maxItems": 0}}},
                },
            ],
        }
        candidate = {
            "type": "object",
            "additionalProperties": False,
            "required": ["index", "excerpt_hash", "locator"],
            "properties": {
                "index": {"type": "integer", "minimum": 1},
                "excerpt_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "locator": {"type": "string", "pattern": "^lines:[1-9][0-9]*-[1-9][0-9]*$"},
            },
        }
        concept = {
            "type": "object", "additionalProperties": False,
            "required": ["title", "definition", "provider", "confidence"],
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 100},
                "definition": source_statement,
                "provider": {"type": "string", "minLength": 1, "maxLength": 200},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        }
        relation = {
            "type": "object", "additionalProperties": False,
            "required": ["source_concept", "target_concept", "relation", "provider", "confidence"],
            "properties": {
                "source_concept": {"type": "string", "minLength": 1, "maxLength": 100},
                "target_concept": {"type": "string", "minLength": 1, "maxLength": 100},
                "relation": source_statement,
                "provider": {"type": "string", "minLength": 1, "maxLength": 200},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        }
        skill = {
            "type": "object", "additionalProperties": False,
            "required": [
                "title", "positioning", "scenarios", "input_requirements", "decision_flow", "actions",
                "output_format", "boundaries", "prohibited_scenarios", "provider", "confidence",
            ],
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 100},
                "positioning": source_statement,
                "scenarios": {"type": "array", "maxItems": 100, "items": source_statement},
                "input_requirements": {"type": "array", "maxItems": 100, "items": source_statement},
                "decision_flow": {"type": "array", "maxItems": 100, "items": source_statement},
                "actions": {"type": "array", "maxItems": 100, "items": source_statement},
                "output_format": source_statement,
                "boundaries": {"type": "array", "maxItems": 100, "items": source_statement},
                "prohibited_scenarios": {"type": "array", "maxItems": 100, "items": source_statement},
                "provider": {"type": "string", "minLength": 1, "maxLength": 200},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version", "provider", "provenance", "warnings",
                "evidence_selection_candidates", "concepts", "relations", "skills",
            ],
            "properties": {
                "schema_version": {"const": "portable-provider-map-v3"},
                "provider": {"type": "string", "minLength": 1, "maxLength": 200},
                "provenance": {"type": "object"},
                "warnings": {"type": "array", "maxItems": 1000, "items": {"type": "string", "maxLength": 10_000}},
                "evidence_selection_candidates": {"type": "array", "maxItems": 100_000, "items": candidate},
                "concepts": {"type": "array", "maxItems": 100, "items": concept},
                "relations": {"type": "array", "maxItems": 100, "items": relation},
                "skills": {"type": "array", "maxItems": 100, "items": skill},
            },
        }

    @classmethod
    def reduce(
        cls,
        cache_root: Path,
        conversion_id: str,
        chunks: tuple[DocumentChunk, ...],
        map_results: dict[str, dict[str, Any]],
        extraction_hashes: dict[str, str],
        catalog: EvidenceCatalog,
    ) -> tuple[ProviderResult, bool, str]:
        deterministic_chunks = tuple(sorted(chunks, key=lambda item: item.chunk_id))
        ordered_hashes = [extraction_hashes[item.chunk_id] for item in deterministic_chunks]
        key_payload = {
            "conversion_id": conversion_id,
            "extraction_hashes": ordered_hashes,
            "reducer_version": cls.VERSION,
        }
        cache_key = sha256_text(canonical_json(key_payload))
        cache_path = cache_root / "binding-cache" / f"{cache_key}.json"
        if cache_path.is_file():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("cache_key") == cache_key:
                return cls._result_from_dict(cached["result"]), True, cache_key

        result = ProviderResult(provider="map-reduce")
        title_to_skill: dict[str, SkillCandidate] = {}
        semantic_skill_keys: set[str] = set()
        concept_keys: set[str] = set()
        relation_keys: set[str] = set()
        providers: set[str] = set()
        for chunk in deterministic_chunks:
            portable = map_results[chunk.chunk_id]
            cls.validate_portable(portable)
            selection_candidates = portable["evidence_selection_candidates"]
            providers.add(str(portable.get("provider", "unknown")))
            result.warnings.extend(str(item) for item in portable.get("warnings", []))
            for item in portable.get("concepts", []):
                candidate = ConceptCandidate(
                    title=str(item["title"]),
                    definition=cls._rebind_statement(item["definition"], chunk, catalog, selection_candidates),
                    provider=str(item["provider"]), confidence=float(item.get("confidence", 0.5)),
                )
                key = sha256_text(canonical_json({"title": candidate.title, "text": candidate.definition.text}))
                if key not in concept_keys:
                    concept_keys.add(key); result.concepts.append(candidate)
            for item in portable.get("relations", []):
                candidate = ConceptRelationCandidate(
                    source_concept=str(item["source_concept"]), target_concept=str(item["target_concept"]),
                    relation=cls._rebind_statement(item["relation"], chunk, catalog, selection_candidates),
                    provider=str(item["provider"]), confidence=float(item.get("confidence", 0.5)),
                )
                key = sha256_text(canonical_json({
                    "source": candidate.source_concept, "target": candidate.target_concept,
                    "text": candidate.relation.text,
                }))
                if key not in relation_keys:
                    relation_keys.add(key); result.relations.append(candidate)
            for item in portable.get("skills", []):
                candidate = cls._rebind_skill(item, chunk, catalog, selection_candidates)
                semantic_key = cls._skill_semantic_key(candidate)
                if semantic_key in semantic_skill_keys:
                    continue
                normalized_title = re.sub(r"\W+", "", candidate.title).casefold()
                prior = title_to_skill.get(normalized_title)
                if prior is not None and cls._skill_semantic_key(prior) != semantic_key:
                    result.comparisons.append({
                        "kind": "cross-chapter-skill-conflict",
                        "title": candidate.title,
                        "retained_fingerprint": prior.fingerprint(),
                        "held_fingerprint": candidate.fingerprint(),
                        "decision": "first-in-document-retained; divergent candidate held for human review",
                    })
                    result.warnings.append(f"Cross-chapter conflict held for review: {candidate.title}")
                    continue
                semantic_skill_keys.add(semantic_key)
                title_to_skill[normalized_title] = candidate
                result.skills.append(candidate)
        result.provider = "map-reduce:" + "+".join(sorted(providers))
        result.provenance = {
            "provider": result.provider,
            "map_reduce": True,
            "reducer_version": cls.VERSION,
            "conversion_id": conversion_id,
            "extraction_hashes": ordered_hashes,
            "chunk_count": len(chunks),
        }
        cache_record = {
            "schema_version": "binding-cache-v3",
            "cache_key": cache_key,
            "cache_key_fields": key_payload,
            "created_at": now_iso(),
            "result": cls._result_to_dict(result),
        }
        atomic_write(cache_path, json.dumps(cache_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return result, False, cache_key

    @classmethod
    def _rebind_statement(
        cls,
        value: dict[str, Any],
        chunk: DocumentChunk,
        catalog: EvidenceCatalog,
        selection_candidates: list[dict[str, Any]],
    ) -> GroundedStatement:
        origin = str(value.get("origin", "source"))
        indexes = value.get("selected_evidence_indexes", [])
        if origin == "engine_policy":
            if indexes:
                raise EvidenceSelectionError("Engine-policy portable statement selected source Evidence.")
            return GroundedStatement(str(value["text"]), (), "engine_policy")
        selector = EvidenceSelector(
            catalog,
            EvidenceScope(
                catalog.source_id,
                catalog.conversion_id,
                chunk_start_line=chunk.start_line,
                chunk_end_line=chunk.end_line,
                owned_start_line=chunk.owned_start_line,
            ),
            selection_candidates,
        )
        selection = selector.validate(indexes)
        return GroundedStatement(str(value["text"]), selection.evidence_ids, "source")

    @classmethod
    def _rebind_skill(
        cls,
        item: dict[str, Any],
        chunk: DocumentChunk,
        catalog: EvidenceCatalog,
        selection_candidates: list[dict[str, Any]],
    ) -> SkillCandidate:
        statements = lambda key: tuple(
            cls._rebind_statement(value, chunk, catalog, selection_candidates) for value in item[key]
        )
        return SkillCandidate(
            title=str(item["title"]),
            positioning=cls._rebind_statement(item["positioning"], chunk, catalog, selection_candidates),
            scenarios=statements("scenarios"),
            input_requirements=statements("input_requirements"),
            decision_flow=statements("decision_flow"),
            actions=statements("actions"),
            output_format=cls._rebind_statement(item["output_format"], chunk, catalog, selection_candidates),
            boundaries=statements("boundaries"),
            prohibited_scenarios=statements("prohibited_scenarios"),
            provider=str(item["provider"]),
            confidence=float(item.get("confidence", 0.5)),
        )

    @staticmethod
    def _skill_semantic_key(candidate: SkillCandidate) -> str:
        payload = candidate.semantic_payload()
        for field in ("positioning", "output_format"):
            payload[field]["evidence_ids"] = []
        for field in ("scenarios", "input_requirements", "decision_flow", "actions", "boundaries", "prohibited_scenarios"):
            for statement in payload[field]:
                statement["evidence_ids"] = []
        payload.pop("provider", None); payload.pop("confidence", None)
        return sha256_text(canonical_json(payload))

    @staticmethod
    def _result_to_dict(result: ProviderResult) -> dict[str, Any]:
        return {
            "provider": result.provider,
            "provenance": result.provenance,
            "warnings": result.warnings,
            "comparisons": result.comparisons,
            "concepts": [{
                "title": item.title, "definition": item.definition.as_dict(),
                "provider": item.provider, "confidence": item.confidence,
            } for item in result.concepts],
            "relations": [{
                "source_concept": item.source_concept, "target_concept": item.target_concept,
                "relation": item.relation.as_dict(), "provider": item.provider, "confidence": item.confidence,
            } for item in result.relations],
            "skills": [item.semantic_payload() for item in result.skills],
        }

    @staticmethod
    def _result_from_dict(value: dict[str, Any]) -> ProviderResult:
        result = ProviderResult(
            provider=str(value["provider"]), warnings=list(value.get("warnings", [])),
            provenance=dict(value.get("provenance", {})), comparisons=list(value.get("comparisons", [])),
        )
        for item in value.get("concepts", []):
            result.concepts.append(ConceptCandidate(
                str(item["title"]), GroundedStatement.from_dict(item["definition"]),
                str(item["provider"]), float(item.get("confidence", 0.5)),
            ))
        for item in value.get("relations", []):
            result.relations.append(ConceptRelationCandidate(
                str(item["source_concept"]), str(item["target_concept"]),
                GroundedStatement.from_dict(item["relation"]), str(item["provider"]),
                float(item.get("confidence", 0.5)),
            ))
        result.skills = [SkillCandidate.from_payload(item) for item in value.get("skills", [])]
        return result
