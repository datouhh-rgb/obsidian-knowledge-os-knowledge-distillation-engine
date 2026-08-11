# Real Codex Discovery and Trigger Report

Result: `BLOCKED`

Only raw real-Codex sessions count. Unit fixtures and deterministic adapters are never substituted for discovery or implicit-trigger acceptance.
A skipped or unavailable real run remains a release blocker.

## Commands and assertions

### Blocker 3: FAILED

Command: `real Codex repo/user exposure workflow`

Exit code: `1`

- FAIL — real_codex_not_skipped: `explicitly skipped`

### Blocker 4: FAILED

Command: `real Codex 10/10/5 implicit eval + bad-description control`

Exit code: `1`

- FAIL — real_codex_not_skipped: `explicitly skipped`
