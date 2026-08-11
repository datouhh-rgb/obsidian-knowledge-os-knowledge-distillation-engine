# Obsidian Knowledge OS — Knowledge Distillation Engine V3.3.0

V3.3.0 adds the Knowledge Synthesis Layer. It turns stable, Evidence-bound Concepts into reviewable Method Clusters and then into candidate Skills:

```text
Evidence → Concept → Canonicalization → Method Cluster
         → Skill Synthesis → Quality Gate → Candidate Skill
```

The caller-provided Obsidian Vault remains the only knowledge authority. V3.3 does not change Source Identity, Conversion Identity, the V3.2.1 Evidence-selection contract, lifecycle states, approval rules, exposure behavior, or the atomic Vault transaction model.

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
   └─ conversions/<conversion-id>/
10_Reviews/
.knowledge-engine/{STATE.json, journal/, staging/, migrations/}
```

The repository stores engine code, tests, and release evidence; it is not a second knowledge base. Canonical knowledge and Skill files stay in the Vault, and exposure locations contain managed links only.

## Production acceptance

Local verification:

```powershell
python scripts\final_production_check.py --local-only
```

Final verification after the six GitHub Actions jobs and real-book regression evidence are available:

```powershell
python scripts\final_production_check.py --skip-tests `
  --ci-evidence FINAL_PRODUCTION_AUDIT\cross_platform_matrix.json `
  --regression-evidence FINAL_PRODUCTION_AUDIT\stock_operator_v33_regression.json
```

The final gate accepts exactly six successful jobs—Windows, Ubuntu, and macOS with Python 3.11 and 3.12—all attesting the same commit SHA. Local success alone cannot emit `V3.3_KNOWLEDGE_SYNTHESIS_READY`.

See `docs/ARCHITECTURE_V3_3.md`, `REGRESSION_REPORT_V3_3.md`, `SKILL_SYNTHESIS_REPORT.md`, `FINAL_PRODUCTION_REPORT.md`, and `CI_REPORT.md`.
