# Obsidian Knowledge OS — Knowledge Distillation Engine V3.3.0

V3.3.0 adds the Knowledge Synthesis Layer. It turns stable, Evidence-bound Concepts into reviewable Method Clusters and then into candidate Skills:

```text
Original Source → Canonical Converter → ConversionQualityGate
→ Structure Normalizer → Evidence → Concept → Canonicalization → Method Cluster
         → Skill Synthesis → Quality Gate → Candidate Skill
```

The caller-provided Obsidian Vault remains the only knowledge authority. V3.3 Final Production Acceptance does not change Source Identity, Conversion Identity, the V3.2.1 Evidence-selection contract, lifecycle states, approval rules, or the atomic Vault transaction model. Exposure keeps implicit invocation disabled. It uses a true directory symlink where supported; otherwise it creates a read-only, hash-bound, disposable deployment cache whose manifest points back to the approved immutable Vault version. The derived cache is never a knowledge source and cannot sync changes back to the Vault.

## Final production remediation

- AZW3 acceptance now starts from the immutable original and a local KindleUnpack adapter. The previous preserved-Markdown snapshot path is historical-fixture-only and cannot be selected by the production regression command.
- EPUB/KF8 members are decoded in BOM/declaration/meta/strict-UTF-8 order, followed only by audited Chinese fallbacks. Production text never uses replacement decoding.
- `ConversionQualityGate` executes before chunking and LLM calls. A blocker records a conversion diagnostic under `.knowledge-engine/runs` while Graph, Registry, Skill, Source assets, and the Asset Ledger remain untouched.
- Skill signal extraction no longer manufactures sliding Chinese bigrams. Insufficient complete, Evidence-grounded phrases block the action or candidate.
- Skill actions are now Evidence-bound procedural abstractions. Raw Evidence excerpts, narrative quotations, and generic policy shells cannot pass the semantic release gate.
- Method Clusters require explainable multi-signal cohesion that includes a semantic signal; sharing only a book, purpose label, chapter, or action word is insufficient.
- Conversion/front-matter metadata is retained as provenance but excluded from body Evidence and Concept promotion.
- Invalid Concept candidates are quarantined individually. The book-level gate blocks only systemic failure and reports accepted, quarantined, rejected, invalid-rate, and chapter-coverage data.
- Authoritative schemas are reproducible from Python models. Git-source and final-archive manifests are separate, and the final archive manifest validates exact bytes.
- Current book evidence is canonicalized to one file per named regression. Previous blocked evidence and hashes remain under `history/`.

## Knowledge Synthesis contracts

- `KnowledgeSynthesizer` groups stable Concepts using semantic similarity, shared Evidence source, chapter context, decision purpose, and action flow. Matching titles alone never cause a merge.
- `MethodCluster` records `id`, title, description, Concept IDs, Evidence IDs, source books, confidence, and the auditable creation reason.
- `SkillSynthesizer` accepts only a Method Cluster plus its system-created Evidence. A Skill requires at least two Concepts and three valid Evidence records.
- Existing structure, Evidence, semantic, executability, quality, graph, and production gates remain authoritative. A rejected synthesized Skill stays blocked without weakening any gate.
- Method notes link to Concepts and Evidence; candidate Skill notes link back to their Method and Concepts.
- The graph keeps schema version 3 and adds compatible `Concept supports Method` and `Method generates Skill` edges.
- Every synthesized Skill starts as `candidate`. Nothing is auto-approved, auto-exposed, or connected to a project.

Rule Analyzer remains diagnostic-only. Hybrid Analyzer is the recommended production mode, and its LLM transport remains Evidence-select-only: the model may return only `selected_evidence_indexes`; the engine creates and validates all Evidence identities.

## Install

Requires Python 3.11 or 3.12 and Git.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m pytest -q
```

No `PYTHONPATH` or manual path modification is required.

## Minimal workflow

```powershell
$vault = "C:\path\to\existing-obsidian-vault"

knowledge-engine doctor --vault $vault
knowledge-engine inspect ".\examples\source-grounded-handbook.md" `
  --vault $vault --analyzer rule

$llmCommand = "python .\path\to\local-evidence-selector.py"
knowledge-engine distill ".\examples\source-grounded-handbook.md" `
  --vault $vault --analyzer hybrid --llm-command $llmCommand

knowledge-engine integrity --vault $vault
```

Distillation writes only to the selected Vault. Approval and link-only exposure remain separate human-authorized commands.

## Vault-owned assets

```text
05_Knowledge/
├─ Cards/
├─ Concepts/
├─ Methods/
├─ Cases/
├─ Graph/{concept-graph.v3.json, Concept-Graph-V3.md}
├─ Skills/
│  ├─ REGISTRY_V3.json
│  ├─ EXPOSURES.json
│  ├─ SYNTHESIS_PROPOSALS.json
│  └─ <skill>/{SKILL.md, agents/openai.yaml, references/, versions/}
└─ Audit/{ASSET_LEDGER.json, Events/}
06_Sources/Books/
├─ SOURCE_INDEX.json
└─ <title>--<source-id>/
   ├─ originals/
   └─ conversions/<conversion-id>/{CONVERSION.json, CONVERSION_QUALITY.json, ...}
10_Reviews/
.knowledge-engine/{STATE.json, journal/, staging/, migrations/}
```

The repository stores engine code, tests, and release evidence; it is not a second knowledge base. Canonical knowledge and Skill files stay in the Vault. Exposure locations contain either a managed link or a derived, read-only deployment cache with an ownership manifest and drift detection.

## Production acceptance

Engine verification:

```powershell
python scripts\final_production_check.py --local-only
```

Fail-closed final verification after current six-job CI, both real-book regressions, and real Codex evidence are available:

```powershell
python scripts\final_v33_acceptance.py `
  --ci-evidence release\evidence\ci\cross_platform_matrix.json
```

The final gate accepts exactly six successful portable jobs—Windows, Ubuntu, and macOS with Python 3.11 and 3.12—all attesting the same immutable merged/tagged commit SHA. It separately requires canonical original-book conversion, Windows Real Codex Discovery, explicit `$skill` loading with a successful target `SKILL.md` read event, both named real-book regressions, release/history isolation, and all tests. Blocked components still upload atomic machine evidence and may create only a `BLOCKED-Audit.zip`; they cannot create a Production-Ready archive. The production profile is `explicit_only`; implicit invocation remains disabled and no implicit precision/recall is claimed. The gate emits only `V3.3.0_PRODUCTION_READY` or `V3.3.0_BLOCKED`.

Current reports are under `release/`. Prior V3.1/V3.2 audits are isolated under `history/`. The authoritative decision is `FINAL_PRODUCTION_ACCEPTANCE_REPORT.md`.
