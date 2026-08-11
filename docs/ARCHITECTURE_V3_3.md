# V3.3 Knowledge Synthesis Architecture

## Scope

V3.3 adds one processing layer between canonical Concepts and candidate Skills. The selected Obsidian Vault remains the sole persistent knowledge authority.

```text
system Evidence
  ↓
Concept Candidate
  ↓ Concept Canonicalizer + Concept Quality Gate
stable Canonical Concept
  ↓ KnowledgeSynthesizer
Method Cluster
  ↓ SkillSynthesizer
Skill Candidate
  ↓ existing semantic, quality, graph, and production gates
review-only Vault asset
```

Source Identity, Conversion Identity, Evidence index selection, lifecycle states, approval/exposure rules, and Vault transaction semantics are inherited unchanged from V3.2.1.

## Method Cluster

`MethodCluster` is immutable and contains:

- `id`
- `title`
- `description`
- `concept_ids`
- `evidence_ids`
- `source_books`
- `confidence`
- `creation_reason`

The synthesizer profiles only stable Concepts whose Evidence resolves inside the active catalog. It combines five signals: semantic similarity, common Evidence source, chapter context, decision purpose, and action flow. The `creation_reason` records the observed signal values. A shared title is never a merge rule.

A cluster requires at least two Concepts and three unique Evidence records. Duplicate candidate clusters with 75% or greater Concept-set overlap are retained only once, and a run emits at most eight clusters.

## Skill synthesis and gates

`SkillSynthesizer` creates the full existing `SkillCandidate` structure: positioning, scenarios/trigger, inputs, decision flow, source actions, output, boundaries, prohibited scenarios, provider provenance, and Evidence bindings.

The synthesis gate is additive. It cannot replace or relax:

- Skill structure validation
- Evidence scope validation
- semantic support validation
- executability validation
- quality scoring
- graph dry-run
- Skill Production Gate

If a synthesized candidate fails an existing gate, that candidate is removed and recorded in `KNOWLEDGE_SYNTHESIS.json` as blocked. Other independent candidates may continue; no blocked candidate enters the Registry, graph, Skill directory, or ledger.

Every accepted output is still a `candidate`. Approval, exposure, and project connection remain separate human actions.

## Vault and graph integration

V3.3 adds Method notes under `05_Knowledge/Methods`. Each Method note links to its Concepts and Evidence, and each synthesized Skill links back to its Method and Concepts.

Graph schema version 3 remains unchanged. Compatible nodes and edges add the four-level trace:

```text
Evidence → Concept → supports → Method → generates → Skill
```

The graph dry-run validates identities and Evidence scope before Registry materialization. The source bundle, Concepts, Methods, Skills, Registry, graph, and ledger are committed by the existing atomic Vault transaction.

## Failure boundary

- Invalid or out-of-scope Evidence blocks the candidate or run according to the existing fail-closed contract.
- Unresolved Concept conflicts are isolated from Method/Skill synthesis and remain visible as review warnings.
- Low-cohesion groups do not form Methods.
- Low-quality synthesized Skills stay blocked; the engine does not lower thresholds to reach a target count.
- No operation writes to a second knowledge base or copies canonical Skill files into an Agent directory.
