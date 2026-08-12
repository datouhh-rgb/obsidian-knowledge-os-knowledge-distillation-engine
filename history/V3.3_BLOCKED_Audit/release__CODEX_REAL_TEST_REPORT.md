# V3.3.0 Real Codex Integration Report

## Scope

The verification used installed `codex-cli 0.144.6`, actual local Exposure paths, disposable Vaults, independent Codex sessions, and retained raw JSONL transcripts. It did not use mock discovery, fixture discovery, or synthetic trigger results. The loading contract was checked against the official [Build skills](https://learn.chatgpt.com/docs/build-skills) documentation.

## Candidate

- Book: 《股票大作手回忆录》
- Upstream run: `run-0612616f2d21999e726a3ba1`
- Source ID: `75dba888e796b4aae0ef85f99798b6c8eab002e2c2b213e98b9b3e5d5c32b828`
- Conversion ID: `d563d127872852958eb4b6e311258407b5865229e0eb52342963c113ad0a25e3`
- Candidate fingerprint: `fae402a49b5d7993097dea72ba2af88ec4ad89773c959aa693ecec6c44f9aede`
- Skill: `apply-source-method-a4e20f8d-75dba888`
- Lifecycle: `candidate`
- Evidence bindings: 3
- Implicit invocation: `false`

The candidate was replayed exactly from preserved V3.3 regression evidence. The source hash was verified before and after the test.

## Human-review attempt

- Reviewer: `Andy / V3.3 production acceptance`
- Timestamp: `2026-08-12T09:48:16+08:00`
- Reason: `Exact preserved candidate passed real Codex explicit-only evaluation and all Evidence/quality gates.`
- Automatic: no
- Accepted for release: no

A later strict transcript review found that the legacy detector counted failed path-search commands as Skill reads. The disposable approval attempt was invalidated. The exported example remains a candidate, and no approval was written to the production Vault.

## Results

| Check | Result |
|---|---|
| Managed Exposure created | PASS |
| Exposure link health | PASS |
| Skill file copied | NO |
| Static metadata validation | PASS |
| Implicit invocation remained OFF | PASS |
| Exact Skill discovery | FAIL |
| Explicit `$apply-source-method-a4e20f8d-75dba888` load | FAIL |
| Positive trigger | FAIL |
| Negative non-invocation | PASS |
| Bad-description negative control | PASS |
| Exposure removed after test | PASS |

The explicit session returned `Skill unavailable`. The engine could resolve the Windows directory junction, but Codex did not include the exact Skill in its discovered list and did not traverse the junction while searching. A true directory-symlink probe failed because the host lacked Administrator/Developer Mode permission. No external privilege change was made.

## Evidence

- Strict machine audit: `release/evidence/codex_real_test/codex_real_integration.json`
- Pre-strict audit: `release/evidence/codex_real_test/codex_real_integration.pre-strict.json`
- Raw sessions: `release/evidence/codex_real_test/raw/`
- Retained raw files: SHA-256 bound in the strict audit
- Production Vault opened: no
- Project connections: none

## Result

`V3.3.0_BLOCKED`

Real Codex Discovery, positive triggering, and explicit loading are not proven. No release-ready claim is permitted.
