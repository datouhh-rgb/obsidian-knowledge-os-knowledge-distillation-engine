# WAL Fault-injection Report

Result: `PASSED`

Recovery compares target bytes with both before and after hashes and fails closed when neither matches.
The stale-applied-marker window and operation/payload-bound idempotency are independent gates.

## Commands and assertions

### Blocker 8: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_lifecycle_transaction.py::test_fault_injection_rolls_back_all_core_writes tests/test_lifecycle_transaction.py::test_crash_wal_recovery_restores_consistent_state tests/test_v31_acceptance_controls.py::test_wal_recovers_after_bytes_replace_with_stale_marker tests/test_v31_acceptance_controls.py::test_wal_neither_before_nor_after_requires_manual_recovery`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/08/stdout.txt` — `bf1029329da50be45df3fe1b5359f6c1869078fd5474e02573b842f32f5f8c7e`
- `FINAL_AUDIT_PACK/blockers/08/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

### Blocker 9: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_v31_acceptance_controls.py::test_idempotency_key_binds_operation_and_payload`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/09/stdout.txt` — `d40f86cec22ce9eff8b5bcb1061ca911ae5ea7854fd90fa8be313cf1e0e05292`
- `FINAL_AUDIT_PACK/blockers/09/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
