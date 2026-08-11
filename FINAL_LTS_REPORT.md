# FINAL LTS REPORT

LTS status: `V3.1_LTS_BLOCKED`

Engine package version: `3.1.1`

Generated: `2026-08-11T06:14:28.585648Z`

Local platform: `Windows-10-10.0.19045-SP0`

Python: `3.11.9`

## Scope

V3.1.1 is a stability-only patch. It adds no knowledge feature, database, module, data-model change, or lifecycle transition.

## Check results

| Check | Result | Exit | Reason |
|---|---|---:|---|
| manifest_hash | PASS | 0 | 73 files verified |
| compile_check | PASS | 0 | command completed successfully |
| pytest | PASS | 0 | 92 passed, 1 skipped in 120.52s (0:02:00) |
| cross_platform | FAIL | 1 | verified six-job CI evidence was not supplied |
| evidence_validation | PASS | 0 | 11 passed in 14.49s |
| skill_validation | PASS | 0 | 2 passed in 6.00s |
| migration_dry_run_apply_rollback | PASS | 0 | 1 passed in 5.63s |
| exposure_create_discover_remove_recreate | PASS | 0 | 4 passed in 5.55s |
| rollback_test | PASS | 0 | 8 passed in 13.82s |

## Failures and repair suggestions

### cross_platform

Command: `verify --cross-platform-evidence`

Reason: verified six-job CI evidence was not supplied

Suggested repair: Run the official GitHub Actions six-job matrix on Windows, Ubuntu, and macOS with Python 3.11/3.12, then pass its consolidated JSON evidence.

## Decision blockers

- `failed:cross_platform`

## Known limitations

- A local run cannot prove other operating systems; `V3.1_LTS_READY` requires consolidated evidence from all six CI jobs.
- Windows directory links may use junction fallback when symbolic-link permission is unavailable.
- macOS support is accepted only after its Python 3.11 and 3.12 CI jobs pass.

## Stability changes

- Unified symlink/junction/directory cleanup behind `remove_exposure_link()`.
- Added create/discover/remove/recreate exposure regressions.
- Added explicit fail-closed LTS checks for pytest, Evidence, Skill validation, migration, exposure, rollback, compilation, and manifest integrity.
