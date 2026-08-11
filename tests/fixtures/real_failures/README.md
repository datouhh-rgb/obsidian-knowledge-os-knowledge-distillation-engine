# Immutable real-failure fixtures

These fixtures preserve the exact V3.1.1 run manifests and Case A binding cache by byte copy. The SHA-256 values in each `fixture.json` are regression assertions. Tests read them but never rewrite them.

- Case A: graph edge identity collision from `run-7702fa58a7033e0bca678f8d`.
- Case B: unknown local Evidence from `run-c6518aded53a1ef9a90dbf59` and the missing original-AZW3 identity boundary.
- Case B-EV629: LLM-created Evidence `ev-629` from `run-a80c81c42c1f319c7a9c50ff`. The readable JSON copy is retained beside a base64 byte copy whose decoded SHA-256 exactly matches the historical run manifest.

The source books are not redistributed in this repository. The fixtures contain only the pre-existing engine diagnostics needed to reproduce the contracts.
