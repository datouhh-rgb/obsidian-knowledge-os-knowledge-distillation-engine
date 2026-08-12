# V3.3 Knowledge Synthesis Test Results

- Local platform: Windows
- Python: 3.11
- Editable install without `PYTHONPATH`: PASS
- Clean-install regression: PASS
- Compile check: PASS
- Full local pytest: `129 passed, 1 skipped in 325.10s`
- Required Knowledge Synthesis tests: PASS
- Protected V3.2.1 contract hashes: PASS (8/8 unchanged)
- Real-book Hybrid regression: PASS (42/42 chunks)
- Method Clusters: 5
- Skill Candidates: 3, all `candidate`
- Local production gate: `V3.3_LOCAL_CHECK_PASS`
- GitHub Windows/Ubuntu/macOS × Python 3.11/3.12: PASS (6/6)
- GitHub aggregate gate: PASS
- Tested commit: `70a6898bd288534f4be80bc6bce5837aa4184d84`
- Formal release state: `V3.3_KNOWLEDGE_SYNTHESIS_READY`

The skipped test is the existing platform-specific branch reported by pytest. No failure was hidden, waived, or converted to a pass.
