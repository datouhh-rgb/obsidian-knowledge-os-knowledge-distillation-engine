# V3.3.0 CI Report

## Workflow

- Workflow: `.github/workflows/v3.3-knowledge-synthesis.yml`
- Matrix: Windows, Ubuntu, macOS × Python 3.11 and 3.12
- Per-job commands: editable install, full pytest, production check
- Aggregate gate: `scripts/final_v33_acceptance.py`

## Current verification commit

- Git commit SHA: pending verification commit
- GitHub Actions URL: pending
- Windows / Python 3.11: pending
- Windows / Python 3.12: pending
- Ubuntu / Python 3.11: pending
- Ubuntu / Python 3.12: pending
- macOS / Python 3.11: pending
- macOS / Python 3.12: pending

The workflow is fail-closed. Each job uploads its own outcome even when pytest or the production check fails, and the aggregate gate requires exactly six passing jobs bound to one commit SHA.

## Current result

`V3.3.0_BLOCKED`

The current real-Codex audit is blocked, so the formal integration tests cannot pass until genuine Discovery and explicit loading evidence exists.
