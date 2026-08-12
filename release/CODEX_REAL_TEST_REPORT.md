# V3.3.0 Real Codex Integration Report

## Scope

This verification used the installed `codex-cli 0.144.6`, real local Skill exposure paths, raw Codex JSONL transcripts, and a disposable Vault. It did not use mock discovery, fixture discovery, or synthetic trigger results. The authoritative Codex Skill loading contract used for the check is [Build skills](https://learn.chatgpt.com/docs/build-skills).

## Candidate under test

- Book: 《股票大作手回忆录》
- Upstream real-book run: `run-0612616f2d21999e726a3ba1`
- Source ID: `75dba888e796b4aae0ef85f99798b6c8eab002e2c2b213e98b9b3e5d5c32b828`
- Conversion ID: `d563d127872852958eb4b6e311258407b5865229e0eb52342963c113ad0a25e3`
- Candidate fingerprint: `fae402a49b5d7993097dea72ba2af88ec4ad89773c959aa693ecec6c44f9aede`
- Exported example: `release/SKILL_EXAMPLE/apply-source-method-a4e20f8d-75dba888/`
- Exported lifecycle: `candidate`
- Evidence bindings: 3
- Implicit invocation: `false`

The candidate was replayed exactly from the preserved V3.3 regression evidence. Its original AZW3 hash was verified before and after the test.

## Human-review attempt

A disposable approval flow recorded reviewer, timestamp, and reason only after a 25-session real-Codex evaluation. A later strict transcript review found that the legacy detector counted failed path-search commands as Skill reads. The attempted approval was therefore invalidated as release proof. The disposable Vault was deleted, and no approval was written to the production Vault or exported Skill example.

The exported release example remains `candidate`; approved count is zero.

## Exposure and discovery result

- Managed Exposure created: PASS
- Exposure health reconciliation: PASS
- `SKILL.md` copied: no
- Implicit invocation remained OFF: PASS
- Exposure removed after test: PASS
- Codex initial discovery: FAIL
- Explicit `$apply-source-method-a4e20f8d-75dba888` load: FAIL
- Negative non-invocation: PASS
- Bad-description decoy non-invocation: PASS

The raw explicit session returned `Skill unavailable`. Windows Exposure used a directory junction. The junction resolved correctly for the engine, but Codex did not include the Skill in its discovered Skill list and its file search did not traverse the junction.

A direct directory-symlink capability probe failed with `Administrator privilege required for this operation`. Enabling Developer Mode or privileged symlink creation is an external operating-system state change and was not performed.

## Evidence

- Machine audit: `release/evidence/codex_real_test/codex_real_integration.json`
- Raw sessions: `release/evidence/codex_real_test/raw/`
- Every retained raw transcript has a recorded SHA-256 value in the machine audit.
- Production Vault opened: no
- Project connections: none

## Result

`V3.3.0_BLOCKED`

Real Codex Discovery and explicit Skill loading are not proven on the current Windows host. No release-ready claim is permitted.
