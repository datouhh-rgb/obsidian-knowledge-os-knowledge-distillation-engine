# V3.3.0 CI Report

## Workflow

- Workflow: `.github/workflows/v3.3-knowledge-synthesis.yml`
- Matrix: `windows-latest`, `ubuntu-latest`, `macos-latest` × Python `3.11`, `3.12`
- Each job performs editable installation, `pytest -q`, and the full `python scripts/final_production_check.py` without `--skip-tests`.
- Each job uploads immutable JSON and engine-check logs.

## Current completed formal evidence

- Actions run: https://github.com/datouhh-rgb/obsidian-knowledge-os-knowledge-distillation-engine/actions/runs/31563583747
- Source branch commit: `a83d98b4ff1dd58b74456dc2376a4605f157e5ee`
- Actions-tested PR merge commit: `691c7b95c69d0bbe54c314ee9b211e915cbe0519`
- Machine evidence: `release/evidence/ci/cross_platform_matrix.json`

| OS | Python | Editable install | pytest | Production check | Job |
|---|---:|---|---|---|---|
| Windows | 3.11 | PASS | FAIL | PASS | BLOCKED |
| Windows | 3.12 | PASS | FAIL | PASS | BLOCKED |
| Ubuntu | 3.11 | PASS | FAIL | PASS | BLOCKED |
| Ubuntu | 3.12 | PASS | FAIL | PASS | BLOCKED |
| macOS | 3.11 | PASS | FAIL | PASS | BLOCKED |
| macOS | 3.12 | PASS | FAIL | PASS | BLOCKED |

All six pytest runs rejected the same stored production evidence: real Codex Discovery, explicit invocation, and positive-trigger proof were absent. Editable installation succeeded in every job. Windows 3.12 additionally recorded one Vault process-lock timeout in `test_twenty_concurrent_usage_events_are_consistent`; that platform-specific failure remains a separate stability blocker and was not suppressed.

## Patch verification result

The workflow command ran the full production checker in every matrix job. All six immutable artifacts were uploaded and consolidated. Matrix result: `0/6 PASS`.

`V3.3.0_BLOCKED`
