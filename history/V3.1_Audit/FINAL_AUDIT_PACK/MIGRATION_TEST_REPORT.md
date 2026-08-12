# V2/V3 Migration Report

Result: `PASSED`

Dry-run, apply, rollback, repeated execution, and source-byte preservation are tested as one release gate.

## Commands and assertions

### Blocker 14: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_chunking_migration.py::test_v2_migration_dry_run_apply_rollback_and_idempotency`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/14/stdout.txt` — `77867685350b6877d0d59307683c52e13b4367bc73e0ba811df040b46ae5a6c7`
- `FINAL_AUDIT_PACK/blockers/14/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
