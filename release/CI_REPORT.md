# V3.3.0 CI Report

## Workflow evidence

- Workflow: `.github/workflows/v3.3-knowledge-synthesis.yml`
- Matrix: Windows、Ubuntu、macOS × Python 3.11、3.12
- Source branch commit: `1b18c1a2481084f42a8b496a6fef88d326b4ece0`
- Actions-tested PR merge commit: `fd30b6f25ffab7db26ae7c879e2b62a97554c080`
- GitHub Actions: https://github.com/datouhh-rgb/obsidian-knowledge-os-knowledge-distillation-engine/actions/runs/31556058977
- Machine evidence: `release/evidence/ci/cross_platform_matrix.json`

Every job executed editable installation, full pytest, and the production check. The Windows jobs first required Git long-path support so the retained immutable transcript paths could be checked out.

## Matrix summary

| OS | Python | Editable install | pytest | Production check | Job |
|---|---:|---|---|---|---|
| Windows | 3.11 | PASS | FAIL | PASS | BLOCKED |
| Windows | 3.12 | PASS | FAIL | PASS | BLOCKED |
| Ubuntu | 3.11 | PASS | FAIL | PASS | BLOCKED |
| Ubuntu | 3.12 | PASS | FAIL | PASS | BLOCKED |
| macOS | 3.11 | PASS | FAIL | PASS | BLOCKED |
| macOS | 3.12 | PASS | FAIL | PASS | BLOCKED |

All six pytest runs failed for the same retained release evidence: five real-Codex integration tests were blocked because the exact Skill was not genuinely discovered or explicitly loaded. The clean-install test consequently failed because its isolated nested suite correctly propagated the same blockers. A representative summary is `1 failed, 128 passed, 1 skipped, 5 errors`.

## Result

`V3.3.0_BLOCKED`

The six-environment workflow ran completely, but the requirement that all six jobs pass was not met.
