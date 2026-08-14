# V3.3.0 Final Production Acceptance Reviewer Brief

This candidate is accepted only when one immutable merged commit or protected release tag proves all of the following:

1. Six portable CI jobs pass on Windows, Ubuntu, and macOS with Python 3.11 and 3.12.
2. The original 《股票大作手回忆录》 AZW3 is read by the canonical KindleUnpack adapter; no preserved Markdown snapshot is used as production input.
3. `ConversionQualityGate` passes before chunking and LLM calls, with zero replacement characters, mojibake blockers, and provenance-to-Concept leakage.
4. Both real-book regressions complete on the same commit with valid Evidence, Concept, Method, Graph, and transaction evidence.
5. A real Candidate is explicitly approved by a human reviewer, deployed as one immutable approved version, discovered and explicitly invoked by Codex, then unexposed with no drift.
6. The exact final archive passes its manifest on Windows, Ubuntu, and macOS and has an adjacent real SHA-256 record.

Review current evidence under `release/`. Historical failures remain under `history/` and never determine the current report by filename alone.

The only successful terminal state is:

```text
V3.3.0_PRODUCTION_READY
```

Any missing or failed gate must remain:

```text
V3.3.0_BLOCKED
```
