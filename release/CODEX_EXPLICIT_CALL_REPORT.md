# V3.3.0 Explicit Skill Call Report

## Test

The real Codex CLI received an explicit `$apply-source-method-a4e20f8d-75dba888` request from the repository containing the managed junction exposure.

- Process exit code: `0`
- Final model response: `SKILL_NOT_AVAILABLE:apply-source-method-a4e20f8d-75dba888`
- Final response SHA-256: `9f339784e4249101062ea7ecdb2c9aab49cd6148991682cc2c76c74037c04405`
- Successful target `SKILL.md` read event: no
- Skill execution: not started

An exit code of zero is not treated as acceptance because the model explicitly reported that the Skill was unavailable and no successful content-read event exists.

## Result

`BLOCKED`

