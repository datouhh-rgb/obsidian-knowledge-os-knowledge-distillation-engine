# Independent Adversarial Regression Report

Result: `PASSED`

The independent defect scenarios were converted to safe-behavior pytest regressions with one blocker command per concern.
The task-referenced `v3_independent_adversarial_audit.py` was not present in the supplied workspace; therefore no claim is made that the absent file itself was executed.

## Commands and assertions

### Blocker 5: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_v31_acceptance_controls.py::test_record_eval_rejects_boolean_only_and_missing_provenance tests/test_v31_acceptance_controls.py::test_old_artifact_eval_cannot_be_recorded`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/05/stdout.txt` — `cf9002045f21ef10c76ed17d11fec8f24ddb95ac16f0ab3dc4f1d24b838eed6b`
- `FINAL_AUDIT_PACK/blockers/05/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

### Blocker 6: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_v31_acceptance_controls.py::test_low_executability_cannot_approve tests/test_lifecycle_transaction.py::test_approval_requires_eval tests/test_quality_graph_provider.py::test_partial_and_conflict_require_human_review`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/06/stdout.txt` — `5ae26070f846990f86571ef5ad9b19e37abd9920f114e91c40a532cd4b17263a`
- `FINAL_AUDIT_PACK/blockers/06/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

### Blocker 7: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_v31_acceptance_controls.py::test_100k_llm_e2e_uses_true_map_reduce_and_rebinds_current_evidence`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/07/stdout.txt` — `8788a962d5f1459cb0ee29cbef696bd2e96af9bf694629d50b74027cc0768c99`
- `FINAL_AUDIT_PACK/blockers/07/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

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

### Blocker 10: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_identity_evidence.py::test_any_byte_tamper_fails_integrity tests/test_v31_acceptance_controls.py::test_asset_ledger_and_strict_schema_detect_metadata_tamper`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/10/stdout.txt` — `9228d326f9cca5250cefeffd359aa0c5a089442ba82ea2dda3e291f671692a76`
- `FINAL_AUDIT_PACK/blockers/10/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

### Blocker 11: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_v31_acceptance_controls.py::test_different_titles_with_high_action_overlap_create_proposal`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/11/stdout.txt` — `ff31e902bd9e23e785e90c40101f9cc9887d0f01cc74c2a1e8ad3fadf9cfe284`
- `FINAL_AUDIT_PACK/blockers/11/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

### Blocker 12: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_v31_acceptance_controls.py::test_semantic_review_is_immutable_event_not_assessment_edit`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/12/stdout.txt` — `06c187720de06339ed01b767991c6f1e71b3208e10025a1e56f938052e3eaa13`
- `FINAL_AUDIT_PACK/blockers/12/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

### Blocker 13: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_synthesis_versioning.py::test_synthesis_requires_two_sources_and_human_approval tests/test_security.py::test_malicious_llm_payloads_are_blocked`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/13/stdout.txt` — `377084747a6cb3df5c20c215b1d01f0cd9c6a199d67be32a1c358417335304c3`
- `FINAL_AUDIT_PACK/blockers/13/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

### Blocker 14: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_chunking_migration.py::test_v2_migration_dry_run_apply_rollback_and_idempotency`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/14/stdout.txt` — `77867685350b6877d0d59307683c52e13b4367bc73e0ba811df040b46ae5a6c7`
- `FINAL_AUDIT_PACK/blockers/14/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

### Blocker 15: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_lifecycle_transaction.py::test_twenty_concurrent_usage_events_are_consistent tests/test_lifecycle_transaction.py::test_same_idempotency_key_counts_once tests/test_v31_acceptance_controls.py::test_cross_process_usage_events_are_consistent`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/15/stdout.txt` — `462eb528b1c4365b8202dd113c927098a023438a7269b2086f16cd2ebf10ebf4`
- `FINAL_AUDIT_PACK/blockers/15/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

### Blocker 16: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_security.py`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/16/stdout.txt` — `a494628ae7fa0497e7146fcd2d7fdbed2d1b988e8bd85aae8afe009e6ac4b212`
- `FINAL_AUDIT_PACK/blockers/16/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
