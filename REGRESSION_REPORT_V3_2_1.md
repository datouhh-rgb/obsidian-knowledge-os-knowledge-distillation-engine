# V3.2.1 Evidence Selection Stability Regression Report

Status: `V3.2.1_EVIDENCE_STABLE`

Package version: `3.2.1`

Unchanged Vault data-contract version: `3.2.0`

## Scope

This patch changes only the Evidence Selection Protocol and its release-verification fixtures. It does not modify Source Identity, Concept Canonicalizer, Graph Contract, Skill Production Gate, lifecycle behavior, the Vault transaction model, or any knowledge schema.

## Implemented stability corrections

- The LLM receives deterministic `Evidence Selection Candidates` with an index, excerpt hash, locator, section, and quoted excerpt. No Evidence ID is exposed.
- The LLM output contract accepts only `selected_evidence_indexes` for grounded statements.
- Recursive pre-validation rejects `evidence`, `evidence_id`, `evidence_ids`, Evidence hashes, locators, and new Evidence objects returned by the LLM.
- `EvidenceSelector.validate()` resolves indexes to system Evidence and blocks unknown indexes, cross-Chunk selection, cross-Conversion selection, source-scope mismatch, and excerpt-hash mismatch.
- Selection counts above 20 produce a review warning; counts above 100 are blocked. Duplicate indexes are deduplicated with a warning.
- Portable map output is now `portable-provider-map-v3`. It contains no Evidence ID and is validated against a fail-closed JSON Schema before cache write and before reduce.
- Retry exhaustion records the root `EvidenceSelectionError`, marks the Chunk `FAILED`, marks the Run `blocked`, and returns a blocked pipeline result before Graph, Registry, Skill, Ledger, or Source writes.

## Immutable regression evidence

The historical failure is preserved as both readable JSON and an exact byte-level base64 fixture:

- Book: 《股票大作手回忆录》
- Historical Run ID: `run-a80c81c42c1f319c7a9c50ff`
- Historical blocker: `AnalyzerProviderError: Evidence selection blocked: LLM attempted to create or select unknown Evidence: ev-629`
- Decoded historical Run SHA-256: `598b2d958aa43e382df54e5cae861b3060de6e9de1c32342c3ca790bb9c1a771`

The original Vault run, old report, old cache, and source file were not edited or deleted.

## Automated verification

- Compile check: PASS.
- Full pytest: `121 passed, 1 skipped` on Windows / Python 3.11.
- Existing skip: one platform-conditional branch; the patch adds no skip.
- New Evidence stability tests: PASS.
- Immutable Case A, original Case B, and EV629 fixture hashes: PASS.
- Unified local production check: `V3.2.1_LOCAL_CHECK_PASS`.

Required tests cover:

1. `test_llm_cannot_create_evidence_id`
2. `test_valid_evidence_index_selection`
3. `test_invalid_evidence_index`
4. `test_cross_chunk_evidence_selection`
5. `test_cross_conversion_evidence_selection`
6. `test_stock_operator_real_regression`
7. `test_retry_does_not_commit_partial_asset`

Additional regressions cover Evidence hash mismatch, abnormal selection count, and portable-provider-map-v3 schema rejection.

## Protected-subsystem verification

The following SHA-256 values were identical before and after the patch:

| Protected subsystem | SHA-256 |
|---|---|
| `identity.py` | `92aca7792a093308189aca635e3cd36052acd99107478b48ca5d756bff55869e` |
| `concepts.py` | `5d91cd8a35947fe3367a2be7ddd7abc6058bde817c22cbb551ff74289cd36f7a` |
| `graph.py` | `42724794240ce754eb4d1ecfbdc947a9a729d2b35350aa8ab273657fede75a26` |
| `production_gates.py` | `e7e7e2bc903b2b74c42801c2cdf30dc8a9da3e862372162be04a312d0bd03df2` |
| `transaction.py` | `705dcbc37bbd0636df96c53642fc654362eebd7a94c472e9c37317f4e97d329d` |

No Vault schema was changed. Generated Vault artifacts deliberately continue to declare the V3.2.0 data-contract version so existing identities and validation contracts are unchanged.

## Real-book result

The full real-book rerun used the preserved AZW3 and conversion snapshot, Codex `gpt-5.6-terra` with low reasoning, the V3.2.1 index-only prompt, and a disposable Vault.

- Run: `run-0612616f2d21999e726a3ba1`
- Chunks: 42/42 completed
- Portable payloads validated: 42
- Unique extraction hashes: 41/41 observed
- Legal Evidence binding occurrences: 387
- `unknown Evidence`: 0
- `ev-629`: 0
- Skills generated: 0
- Formal assets written: 0

The Run completed map/reduce, then the unchanged Concept quality contract blocked the pipeline on 11 same-title semantic conflicts. Twelve fragment warnings were also retained. This is the required fail-closed outcome and was not bypassed to produce Knowledge or a Skill.

The source AZW3 SHA-256 remained `75dba888e796b4aae0ef85f99798b6c8eab002e2c2b213e98b9b3e5d5c32b828`. The live Vault was never opened by the rerun. As an external confirmation, its 68 files under `05_Knowledge` and `06_Sources` had the same combined SHA-256 before and after: `3bf10d12b87d1d07bcdc4d47caacda2bdbed1644955f6401e6ad08844fcb3b18`.

## Known limitations

- The complete GitHub Windows/Ubuntu/macOS × Python 3.11/3.12 matrix was not rerun in this patch task. `V3.2.1_EVIDENCE_STABLE` is the Evidence Selection patch result, not a fabricated cross-platform CI attestation.
- Codex model output still produced ambiguous same-title concepts. The existing Canonicalizer correctly held them for review; this patch does not change that subsystem.
- The real-book verification intentionally prohibited Skill generation. It verifies Evidence selection stability and fail-closed behavior, not Skill usefulness.

## Decision

All Evidence Selection patch acceptance checks passed. The historical failure is preserved, the `ev-629` class of unknown-ID failure is removed from the LLM protocol, invalid selections retry and fail closed without partial assets, and the real book completed all Chunk Evidence bindings legally.

Final patch status: `V3.2.1_EVIDENCE_STABLE`

