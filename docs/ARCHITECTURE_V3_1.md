# V3.1 Architecture

## Authority and trust boundary

The existing Obsidian Vault is the sole persistent authority. The engine repository holds executable code, schemas, tests, and release evidence. Codex user/repository discovery roots contain managed links to Vault Skills only. Providers propose candidates; they cannot directly write authoritative knowledge.

## Main path

```text
immutable source
→ immutable conversion + Evidence catalog
→ chapter-aware bounded chunks
→ provider map + content-addressed extraction cache
→ current-conversion Evidence rebinding
→ deterministic reduce / duplicate / conflict handling
→ strict candidate schema
→ EvidenceGate
→ SemanticSupportGate
→ ExecutabilityGate
→ SkillValidator
→ immutable version seal + asset-ledger update
→ real artifact-bound trigger evaluation
→ explicit human approval
→ link-only exposure
→ project use / immutable review / improvement proposal
```

## Core invariants

| Concern | Invariant |
|---|---|
| Source | Originals and conversion snapshots are never edited in place. |
| Evidence | Every source-derived claim binds to the current conversion's Evidence IDs. |
| LLM | Receives bounded chunks and may propose only methods, relations, and Skill candidates. |
| Candidate | Structure, Evidence, semantic support, executability, and validator gates all pass before sealing. |
| Approval | Exact artifact/version has a real observed evaluation, 100-point production dimensions, and no unresolved review or duplicate blocker. |
| Synthesis | Similarity creates a proposal; it never merges or overwrites source candidates automatically. |
| Transaction | WAL stores before/after hashes and bytes; recovery checks actual target bytes and fails closed on ambiguity. |
| Idempotency | A key is bound to operation plus canonical payload hash. |
| Integrity | An external transaction-bound ledger cross-checks every immutable asset and metadata identity. |
| Exposure | Only an approved canonical Vault Skill is linked; normal files/directories are never deleted. |

## Cache model

Extraction cache keys combine normalized chunk content, provider/model/config, prompt/schema version, and chunker version. Cached extraction records portable local locators rather than old conversion Evidence IDs. Binding/reduce cache keys add the current conversion ID and extraction hashes. Changing one chapter therefore reuses unchanged extraction results while rebinding every final statement to current Evidence.

## Lifecycle

Registry records creation, source books, immutable versions, evaluations, approvals, project links, usage, feedback, semantic review events, and improvement proposals. A changed artifact creates a new version and cannot inherit an older evaluation.

## Cross-platform exposure

POSIX and Windows symbolic links are removed with unlink semantics. Windows junctions are removed only after reparse-point and target validation. An entity directory is never removed. Windows and Ubuntu are release platforms; macOS follows the POSIX branch but is not separately production-accepted in V3.1.
