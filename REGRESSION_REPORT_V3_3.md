# V3.3 Real-book Regression Report

## Result

Local real-book regression: `PASS`

Formal release state: `V3.3_BLOCKED` until the required GitHub Actions six-environment evidence is available.

## Source and provenance

- Book: 《股票大作手回忆录》
- Engine package: `3.3.0`
- Data contract: `3.2.0` (unchanged)
- Run ID: `run-0612616f2d21999e726a3ba1`
- Source ID: `75dba888e796b4aae0ef85f99798b6c8eab002e2c2b213e98b9b3e5d5c32b828`
- Conversion ID: `d563d127872852958eb4b6e311258407b5865229e0eb52342963c113ad0a25e3`
- Analyzer: `HybridAnalyzerProvider(LLMAnalyzerProvider)`
- Model provenance: `gpt-5.6-terra`, reasoning `low`
- Prompt: `distillation-map-prompt-v3.2.1-index-selection`
- Portable schema: `portable-provider-map-v3`
- Vault: disposable regression Vault; the live Vault was never opened

The original source hash was identical before and after the run. The preserved historical `ev-629` regression fixture remains unchanged.

## Pipeline

| Stage | Observed result |
|---|---:|
| Chunks | 42 |
| Completed chunks | 42 |
| Failed chunks | 0 |
| Portable payloads checked | 42 |
| Unique extraction hashes expected/observed | 41 / 41 |
| Evidence selection candidates presented | 1,668 |
| Evidence binding occurrences | 361 |
| Concept candidates returned | 292 |
| Fragment candidates rejected | 12 |
| Unresolved Concept conflicts isolated | 13 |
| Method Clusters retained | 5 |
| Unique Concepts represented by Methods | 18 |
| Unique Evidence represented by Methods | 18 |
| Skill Candidates committed | 3 |

Evidence validation passed for every successful chunk. Neither `unknown Evidence` nor `ev-629` appeared, and the LLM transport remained restricted to `selected_evidence_indexes`.

## Knowledge synthesis

| Method Cluster | Confidence | Concepts | Evidence | Skill result |
|---|---:|---:|---:|---|
| 研究与买进的风险执行方法 | 0.6313 | 3 | 3 | blocked by unchanged Skill quality gate |
| 买进与卖出的风险执行方法 | 0.6145 | 3 | 3 | candidate |
| 研究与买进的风险控制方法 | 0.4915 | 6 | 6 | blocked by unchanged Skill quality gate |
| 预期与观察的决策分析方法 | 0.4766 | 5 | 5 | candidate |
| 卖出与买入的执行行动方法 | 0.4759 | 6 | 6 | candidate |

Four additional aggregation groups were rejected before Method creation because they lacked three unique Evidence records or sufficient multi-signal cohesion. These outcomes demonstrate that the engine did not lower its gates to force a target count.

## Vault, graph, and lifecycle

- Graph: committed atomically; no identity collision.
- Required graph relations: `supports` and `generates` present.
- Registry: committed atomically.
- Asset ledger: committed atomically.
- Skill Evidence files: 3/3 valid and scoped to the active source/conversion.
- Lifecycle: all three Skills are `candidate`.
- Auto-approval: none.
- Auto-exposure: none.
- Project connection: none.
- Disposable Vault: removed after the audit.

The complete machine-readable evidence is `FINAL_PRODUCTION_AUDIT/stock_operator_v33_regression.json`.
