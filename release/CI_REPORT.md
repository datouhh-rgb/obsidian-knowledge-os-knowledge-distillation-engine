# V3.3.0 CI Report

## Workflow

- Workflow: `.github/workflows/v3.3-knowledge-synthesis.yml`
- Matrix: `windows-latest`, `ubuntu-latest`, `macos-latest` × Python `3.11`, `3.12`
- Each job performs editable installation, `pytest -q`, and the full `python scripts/final_production_check.py` without `--skip-tests`.
- Each job uploads immutable JSON and engine-check logs.

## Latest completed formal evidence

- Actions run: https://github.com/datouhh-rgb/obsidian-knowledge-os-knowledge-distillation-engine/actions/runs/31556058977
- Actions-tested merge commit: `fd30b6f25ffab7db26ae7c879e2b62a97554c080`
- Machine evidence: `release/evidence/ci/cross_platform_matrix.json`

| OS | Python | Editable install | pytest | Production check | Job |
|---|---:|---|---|---|---|
| Windows | 3.11 | PASS | FAIL | PASS | BLOCKED |
| Windows | 3.12 | PASS | FAIL | PASS | BLOCKED |
| Ubuntu | 3.11 | PASS | FAIL | PASS | BLOCKED |
| Ubuntu | 3.12 | PASS | FAIL | PASS | BLOCKED |
| macOS | 3.11 | PASS | FAIL | PASS | BLOCKED |
| macOS | 3.12 | PASS | FAIL | PASS | BLOCKED |

All six pytest runs rejected the same stored production evidence: real Codex Discovery, explicit invocation, and positive-trigger proof were absent. This is expected fail-closed behavior, not an install or OS-path failure.

## Patch verification status

The workflow command was corrected to run the full production checker in every matrix job. A new run on the patch commit is required after commit and push; it cannot produce `6/6 PASS` while the real-Codex evidence remains blocked.

`V3.3.0_BLOCKED`
