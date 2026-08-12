# V3.1 Final Production Acceptance

Release status: `V3_1_BLOCKED`

| ID | Status | Description |
|---:|---|---|
| 1 | FAILED | Windows and Linux full suites pass with exposure enabled |
| 2 | PASSED | Release manifest contains rerunnable source, audit, schemas, and tests |
| 3 | FAILED | Real Codex repo/user exposure, discovery, explicit invocation, and unexpose preserve Skill bytes |
| 4 | FAILED | Real implicit trigger eval has 10/10/5 raw observed cases and bad description fails |
| 5 | PASSED | Forged, transcript-less, and old-artifact trigger evals cannot approve |
| 6 | PASSED | Low executability, non-pass quality, and unresolved review cannot approve or expose |
| 7 | PASSED | 100k+ main-path map/reduce is chunk-bounded, affected-only, and rebinds current Evidence |
| 8 | PASSED | All transaction crash windows commit fully or hash-reconcile fully, including stale marker |
| 9 | PASSED | Same idempotency key with a different operation or payload conflicts |
| 10 | PASSED | Any byte change across original, SOURCE_TEXT, metadata, catalog, Skill, or references fails integrity |
| 11 | PASSED | Different-title high-overlap methods create a governed synthesis proposal without overwrite |
| 12 | PASSED | Partial/conflict resolution occurs only through immutable human review events |
| 13 | PASSED | Synthesis and source candidates share structure, Evidence, semantic, executable, and validator gates |
| 14 | PASSED | V2/V3 migration dry-run, apply, rollback, repeat, and hash preservation pass |
| 15 | PASSED | Thread/process concurrency, same-key retries, and different keys remain consistent |
| 16 | PASSED | URL, PDF, and LLM security suites pass |
| 17 | PASSED | Release harness emits V3_1_BLOCKED when any gate is deliberately failed |
| 18 | FAILED | V3_1_PRODUCTION_ACCEPTED is emitted only when blockers 1-17 all pass |

## Failed blockers

- 1: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q + verified Linux CI evidence` (exit 1)
  - linux_full_and_exposure_suite: missing
  - no_exposure_skip: tests/test_exposure.py included in full suite
- 3: `real Codex repo/user exposure workflow` (exit 1)
  - real_codex_not_skipped: explicitly skipped
- 4: `real Codex 10/10/5 implicit eval + bad-description control` (exit 1)
  - real_codex_not_skipped: explicitly skipped
- 18: `aggregate exact blocker statuses 1..17` (exit 1)
  - blocker_01_passed: failed
  - blocker_03_passed: failed
  - blocker_04_passed: failed
