# Changelog

## 3.3.0 — Knowledge Synthesis Layer

- Added `KnowledgeSynthesizer` to aggregate stable canonical Concepts into auditable Method Clusters using semantic, Evidence-source, chapter, decision-purpose, and action-flow signals.
- Added the Evidence-bound `MethodCluster` contract and Vault rendering under `05_Knowledge/Methods`.
- Added `SkillSynthesizer` with a fail-closed minimum of two Concepts and three Evidence records while preserving all existing Skill production gates.
- Added candidate-level isolation: a synthesized candidate rejected by an existing semantic or quality gate is recorded as blocked and cannot weaken or bypass that gate.
- Extended the compatible graph contract with `Concept supports Method` and `Method generates Skill` edges without changing graph schema version 3.
- Added the six requested Skill-synthesis regressions plus an end-to-end Vault/Graph/Registry integration test.
- Re-ran the preserved real book 《股票大作手回忆录》 through the Hybrid pipeline: 42/42 chunks completed, five Method Clusters were retained, three Skill Candidates passed, and two candidates remained blocked by the unchanged quality gate.
- Verified all generated Skills remained `candidate`; none was approved, exposed, or connected to a project.
- Added the Windows/Ubuntu/macOS × Python 3.11/3.12 GitHub Actions release workflow and fail-closed V3.3 production gate.

Source Identity, Conversion Identity, Evidence selection, Concept Canonicalizer, lifecycle states, approval/exposure rules, and the Vault transaction model remain unchanged.

## 3.2.1 — Evidence Selection Stability Patch

- Completed final-verification packaging: editable installs now expose both the
  engine package and the existing verification scripts without `PYTHONPATH`.
- Added a clean-venv regression that performs `pip install -e .`, runs the
  complete non-recursive test suite, and executes the local production check.
- Re-ran the preserved stock-operator book through the Hybrid pipeline: all 42
  chunks passed Evidence selection, while the unchanged Concept gate blocked
  the run before any Graph, Registry, Skill, ledger, or formal Vault write.
- Kept the production release decision fail closed because verified six-job
  GitHub Actions evidence has not been supplied.
- Replaced LLM-authored Evidence IDs with system-issued `selected_evidence_indexes`.
- Added deterministic Evidence selection candidates and strict index, chunk, conversion, and excerpt-hash validation.
- Upgraded the map payload to `portable-provider-map-v3` with recursive Evidence-ID rejection and JSON Schema validation.
- Added retry-exhaustion diagnostics that mark the current Chunk `FAILED`, retain `EvidenceSelectionError`, block the Run, and commit no partial Vault asset.
- Preserved `run-a80c81c42c1f319c7a9c50ff` and its `ev-629` failure as an immutable regression fixture.
- Added the requested Evidence selection, cross-scope, real-regression, retry, and no-partial-commit tests.

This patch adds no feature and changes no Source Identity, Concept Canonicalizer, Graph Contract, Skill Production Gate, lifecycle state, Vault transaction model, or knowledge data model.

## 3.2.0 — Real Knowledge Production Layer

- Added immutable original/edition/conversion `SourceIdentity` contracts for PDF, EPUB, AZW3, Markdown, and web ingestion.
- Added conservative structure normalization with locator mapping, repeated-margin cleanup, heading and paragraph recovery, overlap ownership, TOC detection, and explicit image-omission declarations.
- Added Evidence-select-only LLM enforcement at source, conversion, and chunk scope.
- Added deterministic concept canonicalization, unresolved semantic conflicts, and a fragment-aware concept quality gate.
- Made Rule Analyzer diagnostic-only; Hybrid remains the recommended production provider.
- Added graph dry-run before Registry materialization and deterministic, merge-safe edge identities.
- Added a fail-closed Skill Production Gate and removed blocked-bundle writes to the Vault.
- Preserved exact Case A and Case B run diagnostics as immutable SHA-256 regression fixtures.
- Added formal Windows/Ubuntu/macOS × Python 3.11/3.12 GitHub Actions production gating.

No candidate is auto-approved, auto-exposed, or auto-connected to a project.

## 3.1.1 — LTS stability patch

- Added `remove_exposure_link()` with explicit symlink, Windows junction, and directory cleanup branches.
- Preserved the production guard that refuses to remove an entity that replaces a managed exposure link.
- Replaced POSIX-incompatible exposure test cleanup with the shared cross-platform helper.
- Added exposure create/discover/remove/recreate, symlink cleanup, and fail-closed LTS gate regressions.
- Expanded CI to Windows, Ubuntu, and macOS with Python 3.11 and 3.12.
- Added `scripts/final_lts_check.py` for manifest, compilation, pytest, Evidence, Skill, migration, exposure, and rollback checks.

This patch adds no feature and changes no knowledge identifier, Skill version, lifecycle state, data model, or Vault authority rule.

## 3.1.0 — Final production acceptance candidate

- Replaced self-reported trigger tests with observed real-Codex/external-router evaluation artifacts and raw transcript provenance.
- Unified source and synthesis candidates behind strict structure, Evidence, semantic, executability, and validator gates.
- Added artifact-bound approval rules and immutable semantic-review events.
- Wired content-addressed chunk extraction and current-conversion Evidence rebinding into the main map/reduce path.
- Hardened WAL recovery across stale-marker crash windows and operation/payload-bound idempotency.
- Added strict asset schemas and a transaction-bound external SHA-256 ledger.
- Added governed different-title synthesis proposals without source-candidate overwrite.
- Added explicit POSIX/Windows exposure removal behavior and Windows/Linux CI.
- Replaced aggregate self-attestation with 18 independent fail-closed acceptance blockers.

Release status is determined only by `FINAL_AUDIT_PACK/RELEASE_STATUS.txt`; this changelog is not acceptance evidence.
