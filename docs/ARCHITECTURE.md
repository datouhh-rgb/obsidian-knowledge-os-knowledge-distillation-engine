# V3 Production Core Architecture

## Authority boundary

The caller-provided Obsidian Vault is the only persistent knowledge and Skill authority. The repository contains engine code, schemas, tests, and audit evidence; Codex exposure locations contain managed links only. No analyzer or provider writes directly to the Vault.

## Processing path

```text
Preflight existing Vault
  → Immutable ingest (source_id)
  → Immutable conversion snapshot (conversion_id)
  → Stable Evidence catalog (evidence_id)
  → Chapter-aware chunks + resumable cache
  → Rule / LLM / Hybrid candidates
  → Structural Evidence Gate
  → Semantic Support Gate
  → Knowledge and source Skill candidates
  → Duplicate/conflict quality gate
  → Synthesis proposal, if applicable
  → Staged transaction + WAL + CAS commit
  → Version-specific eval + human approval
  → Link-only Codex exposure
  → Version-specific usage/review/improvement
```

## Module boundaries

| Module | Responsibility | Hard boundary |
|---|---|---|
| `ingest.py` | local files, safe public URL, DOCX limits, PDF wrapper contract | no cloud OCR; no private-network fetch |
| `identity.py` | source/conversion identity and collision-free directories | titles never define identity |
| `evidence.py` | stable Evidence and structural integrity | an ID cannot rebind to another excerpt |
| `chunking.py` | stable chunk map, cache, resume, affected-only rerun | interrupted runs are not completed |
| `providers.py` | Rule/LLM/Hybrid candidate production | LLM output never becomes fact directly |
| `semantic.py` | supported/partial/unsupported/conflict assessment | non-supported source claims cannot pass |
| `quality.py` | nine independent quality dimensions | warnings and high duplicate block approval |
| `synthesis.py` | multi-source proposals and approved materialization | same title never overwrites a candidate |
| `registry.py` | immutable versions and separate lifecycle records | new versions start with empty eval/usage |
| `graph.py` | strict governed nodes/edges and human-readable verified view | candidate LLM edges are hidden as facts |
| `transaction.py` | Vault lock, revision/CAS, staging, WAL, rollback/recovery | cross-file mutations cannot partially commit |
| `exposure.py` | user/repo link exposure and reconciliation | no Skill copying; candidate cannot expose |
| `lifecycle.py` | project link, eval, approval, usage, review, rollback | success alone never activates |
| `migration.py` | non-destructive V2 plan/apply/rollback | old V2 bytes are not changed |
| `integrity.py` | end-to-end hash and cross-store reconciliation | a broken chain fails validation |

## Transaction model

Every multi-file mutation obtains `.knowledge-engine/vault.lock`, reads a revision from `STATE.json`, stages new bytes, records a WAL entry, verifies compare-and-swap state, and uses atomic replacement. Failure injection rolls back already-applied writes; simulated process crashes are recovered deterministically by `repair`/`reconcile`. Caller idempotency keys and random UUID event IDs prevent duplicate counts and timestamp collisions.

## Exposure model

The Vault path `05_Knowledge/Skills/<name>` remains canonical. Repo exposure is `<repo>/.agents/skills/<name>` and user exposure is `$HOME/.agents/skills/<name>`. Both must resolve to the canonical Vault directory. Windows tries a symbolic link first and uses a directory junction only when permissions require it. A normal directory at the target is treated as user data and is never deleted.

## Graph and synthesis

Nodes retain source- or Skill-specific identities. Edges carry status, provider, confidence, source/conversion/Evidence, creation time, approver, and version. LLM edges start as `candidate`; Markdown renders only `verified` or `approved` edges. Similar methods create immutable proposals containing common statements, complements, conflicts, scope differences, evidence maps, dispositions, risk and confidence. Human-approved proposals may create a new review version; source candidates remain intact.
