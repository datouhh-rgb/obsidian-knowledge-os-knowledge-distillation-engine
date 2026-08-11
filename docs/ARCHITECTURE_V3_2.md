# V3.2 Real Knowledge Production Architecture

## Authority boundary

The caller-selected Obsidian Vault is the only persistent knowledge authority. V3.2 creates no database and no second Vault. Original inputs are copied byte-for-byte only during a successful atomic knowledge commit. Failed work retains operational diagnostics under `.knowledge-engine/runs`; it does not write a blocked source bundle, knowledge note, graph mutation, Registry record, or Skill artifact.

## Fail-closed flow

```text
Source
  → Source Identity
  → Structure Normalization
  → Chunking and overlap ownership
  → system Evidence generation
  → Evidence validation / LLM selection
  → Concept Canonicalization
  → Concept Quality Gate
  → Knowledge Graph dry-run
  → Skill candidate validation and quality gate
  → atomic candidate commit
  → observed evaluation
  → explicit human approval
  → link-only exposure
```

The approval and exposure stages remain separate lifecycle operations. Distillation never approves, exposes, or attaches a Skill to a project.

## Identity chain

`SourceIdentity` records `source_id`, original SHA-256, source format, converter and version, conversion ID, normalized conversion hash, edition ID, and creation time. For compatibility, an unspecified edition keeps the original SHA-256 as `source_id`; an explicit edition deterministically derives a distinct `source_id` from original hash, format, and edition.

`conversion_id` binds source identity, normalized text, converter, converter version, and converter configuration. Every Evidence ID then binds conversion ID, normalized locator, and normalized excerpt. The Evidence record also carries the conversion locator produced by structure normalization.

## Structure normalization

The normalizer is conservative: it removes only repeated page margins and isolated page-number forms, recognizes headings, joins cross-page heading fragments, dehyphenates line wraps, restores paragraph wraps, records TOC-like entries, and replaces image references with explicit omission declarations. Every normalized line retains the originating conversion-line span.

EPUB uses a bounded standard-library spine reader. AZW3 requires an explicitly configured local converter command; the engine hashes the AZW3 original before conversion and binds the command contract and output hash. PDF continues to require the Microsoft MarkItDown wrapper and validates its source hash and metadata.

## Evidence selection

The LLM receives a finite catalog of system-created Evidence records. `EvidenceSelector` rejects an unknown ID, a different source or conversion, an ID outside the chunk, and an ID owned by a preceding overlap. Portable map caches store validated locator bindings; rebinding to the current conversion repeats the owning-chunk check.

## Concept governance

The canonicalizer is deterministic over input order. It normalizes display variants, merges prefix/pagination truncations only when definitions are compatible, preserves all aliases, mentions, and Evidence IDs, and assigns a stable canonical concept ID. Same-title semantic polarity conflicts produce unresolved `ConceptConflictCandidate` records and block production.

The quality gate uses linguistic completion, definition completeness, Evidence density, duplicate rate, chapter coverage, and procedural signals. It permits grounded legitimate short concepts such as “熵” or “复利” while rejecting discourse fragments such as “这就”.

## Graph and Skill gates

Graph dry-run operates on a deep copy before any Registry materialization. It checks deterministic node and edge identities, duplicate concept aliases, endpoint validity, approval-state consistency, and Evidence scope. Edge identity includes semantic statement identity; compatible repeats merge Evidence and provenance deterministically.

Skill production requires a non-Rule provider, stable accepted concepts, no unresolved conflict, a passed graph dry-run, complete Evidence, supported semantics, executable decision/action fields, explicit scenario/input/output/boundary/prohibition fields, and a quality result that is neither below threshold nor blocked.

## Atomicity and migration

All Vault writes still use the existing write-ahead transaction and ledger. V3.1 metadata remains readable: schemas accept V3.1 and V3.2 engine values, and existing IDs and lifecycle history are never rewritten. V3.2 new conversions emit their expanded identity and locator fields. Migration dry-run/apply/rollback remains part of the complete test suite and CI gate.

## Regression fixtures

`tests/fixtures/real_failures` contains exact byte copies of the two historical run manifests and the Case A binding cache, plus minimal fixture descriptors. Tests verify their SHA-256 before replaying the relevant contracts. The source books themselves are not redistributed.
