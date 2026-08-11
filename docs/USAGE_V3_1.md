# V3.1 CLI Usage

## Distill into an existing Vault

```text
knowledge-engine doctor --vault <vault>
knowledge-engine distill <pdf|md|docx|url|ocr-text> --vault <vault> --analyzer rule|llm|hybrid
knowledge-engine integrity --vault <vault>
```

Input is ingested once, hashed, converted into an immutable snapshot, and transformed into Evidence-bound candidate assets. PDF conversion follows the configured local first-pass converter contract; the source PDF remains unchanged.

## Evaluate and approve a Skill

```text
knowledge-engine skill generate-trigger-cases <skill> --version <v> --vault <vault> --json
knowledge-engine skill run-trigger-eval <skill> --version <v> --cases-json <path> --vault <vault> --json
knowledge-engine skill record-eval <skill> --version <v> --eval-json <path> --vault <vault> --json
knowledge-engine skill approve <skill> --version <v> --reviewer <name> --reason <text> --vault <vault> --json
```

Generation creates expected-only cases: 10 positive, 10 negative, and 5 boundary prompts. Evaluation stores observed routing, Codex/evaluator provenance, stdout/stderr/JSONL, prompt hashes, and transcript hashes. Approval fails if evaluation provenance is forged, raw evidence is absent, the artifact changed, any production quality dimension is below 100, or review items remain open.

## Resolve semantic review items

```text
knowledge-engine skill semantic-review <skill> --version <v> --review-json <file> --reviewer <name> --vault <vault>
```

The command appends an immutable review event containing statement hash, former state, decision, reason, Evidence IDs, artifact hash/version, reviewer, and timestamp. Direct Registry edits are not a supported resolution path.

## Expose, use, and remove

```text
knowledge-engine skill expose <skill> --scope repo --repo <project> --vault <vault> --json
knowledge-engine skill expose <skill> --scope user --vault <vault> --json
knowledge-engine record-use <skill> --version <v> --project <project> --outcome success|failure --feedback <text> --vault <vault>
knowledge-engine skill unexpose <skill> --vault <vault> --json
```

Exposure is allowed only for the approved exact version and creates a managed link to the canonical Vault directory. Unexpose will not delete a normal directory or file.

## Synthesis and migration

```text
knowledge-engine synthesis list --vault <vault>
knowledge-engine synthesis materialize <proposal-id> --vault <vault>
knowledge-engine migrate-v2 --dry-run --vault <vault>
knowledge-engine migrate-v2 --apply --vault <vault>
knowledge-engine migrate-v2 --rollback <migration-id> --vault <vault>
```

Similar concepts or methods create reviewable proposals only. V2/V3 migration is non-destructive, repeatable, hash-checked, and rollback-capable.

## V3.1.1 LTS acceptance

```text
python scripts/final_lts_check.py --cross-platform-evidence <cross-platform-evidence.json>
```

The consolidated evidence must contain all six official Windows/Ubuntu/macOS and Python 3.11/3.12 jobs. Missing evidence produces `V3.1_LTS_BLOCKED`; it is never replaced by local test counts.
