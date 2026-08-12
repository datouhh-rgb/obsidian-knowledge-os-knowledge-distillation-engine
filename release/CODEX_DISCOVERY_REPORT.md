# V3.3.0 Real Codex Discovery Report

## Subject

- Codex CLI: `codex-cli 0.144.6`
- Skill: `apply-source-method-a4e20f8d-75dba888`
- Source chain: 《股票大作手回忆录》 → Evidence → Concept → Method → Candidate Skill
- Implicit invocation: OFF
- Production Vault opened: no

## Actual probes

| Exposure form | Codex available-Skills list | Accepted |
|---|---|---|
| Managed Windows junction | Skill absent | No |
| NTFS hardlink | Skill absent | No |
| Same SKILL.md as regular-file control | Skill present | No — copying is prohibited |

The regular-file control proves that repository skill scanning and the generated metadata are loadable. It is not counted as Exposure proof. A direct true-symlink capability probe failed with `NewItemSymbolicLinkElevationRequired`; the current token lacks permission to create the symlink form supported by Codex.

Raw hashes and the exact platform result are recorded in `release/evidence/codex_real_test/windows_exposure_diagnostics.json`. The earlier real sessions and strict JSONL transcript hashes remain in `release/evidence/codex_real_test/codex_real_integration.json`.

## Result

`BLOCKED`

No mock, fixture discovery, copied Skill, or prefilled result was accepted.

