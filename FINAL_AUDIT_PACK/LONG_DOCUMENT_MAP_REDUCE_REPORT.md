# 100k+ Map/Reduce Report

Result: `PASSED`

The release test runs the actual DistillationPipeline with a contract fake-LLM to prove chunk-bounded map/reduce wiring, affected-only extraction reuse, and Evidence rebinding.
This report does not claim that a deterministic provider is a real external LLM evaluation.

## Commands and assertions

### Blocker 7: PASSED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q tests/test_v31_acceptance_controls.py::test_100k_llm_e2e_uses_true_map_reduce_and_rebinds_current_evidence`

Exit code: `0`

- PASS — command_exit_zero: `0`

Evidence:

- `FINAL_AUDIT_PACK/blockers/07/stdout.txt` — `8788a962d5f1459cb0ee29cbef696bd2e96af9bf694629d50b74027cc0768c99`
- `FINAL_AUDIT_PACK/blockers/07/stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
