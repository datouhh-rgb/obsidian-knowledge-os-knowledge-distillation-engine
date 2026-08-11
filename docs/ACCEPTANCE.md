# V3.1.1 LTS acceptance

Run from the repository root:

```text
python scripts/final_lts_check.py --cross-platform-evidence <cross-platform-evidence.json>
```

The gate requires all of the following independent results:

1. Project manifest generation and hash verification.
2. Python compilation.
3. Full pytest suite.
4. Exact Windows/Ubuntu/macOS × Python 3.11/3.12 CI evidence.
5. Evidence and external asset-ledger integrity.
6. Generated Skill validation and synthesis approval gates.
7. V2/V3 migration dry-run, apply, rollback, and idempotency.
8. Exposure create, discover, remove, and recreate.
9. WAL rollback and crash recovery.

The script decides from named critical checks, not test counts. A missing check, non-zero command, incomplete CI matrix, or hash mismatch returns non-zero and writes `V3.1_LTS_BLOCKED` to `FINAL_LTS_REPORT.md`.

Only an all-pass run prints:

```text
SUCCESS:
V3.1_LTS_READY
```
