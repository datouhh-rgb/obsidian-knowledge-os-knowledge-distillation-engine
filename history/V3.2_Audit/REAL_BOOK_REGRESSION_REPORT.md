# REAL BOOK REGRESSION REPORT

本报告只判断 V3.2.1 对《股票大作手回忆录》的真实书籍回归结果。正式生产发布仍由 `scripts/final_production_check.py` 的全部既有门禁决定。

## 基础信息

- Engine version: `3.2.1`
- Run ID: `run-0612616f2d21999e726a3ba1`
- Source ID: `75dba888e796b4aae0ef85f99798b6c8eab002e2c2b213e98b9b3e5d5c32b828`
- Conversion ID: `d563d127872852958eb4b6e311258407b5865229e0eb52342963c113ad0a25e3`
- Book: `《股票大作手回忆录》`
- Analyzer: `HybridAnalyzerProvider(LLMAnalyzerProvider)`
- Model provenance: `gpt-5.6-terra`，reasoning `low`，由 `codex_index_selection_adapter.py` 隔离调用
- Prompt: `distillation-map-prompt-v3.2.1-index-selection`
- Portable schema: `portable-provider-map-v3`
- Source SHA-256 before/after: 一致
- Conversion snapshot SHA-256: `fe0cacd9cff6e5e3a8d0a712027bfb12339c32730a57c1ebf1002021e24360e8`

历史失败 Run `run-a80c81c42c1f319c7a9c50ff` 仍保留在原位置，文件 SHA-256 为 `598b2d958aa43e382df54e5cae861b3060de6e9de1c32342c3ca790bb9c1a771`，本轮未修改。

## Pipeline

| 阶段 | 结果 | 证据 |
|---|---|---|
| Source ingest | PASS | 原 AZW3 哈希前后相同，未改写原件 |
| Conversion | PASS | 使用同一保存快照，Conversion ID 稳定 |
| Chunk | PASS | 42 总数，42 完成，0 失败 |
| Evidence | PASS | 42 个 portable payload 全部通过；无 unknown Evidence / `ev-629` |
| Concept | FAIL CLOSED | 293 个候选；12 个碎片候选被拒绝；11 个同名语义冲突未自动合并 |
| Graph | PASS / NOT COMMITTED | 未发现 identity collision；因上游质量 blocker 未写入 Graph |
| Skill | NOT GENERATED | 0 个 Skill Candidate，未降低门槛强制产出 |

Pipeline 最终状态为 `blocked`。这是既有 Concept 质量门的预期 fail-closed 结果，不是 Evidence Selection 失败。

## Evidence

- Evidence Selection Candidates: `1668`
- Evidence 绑定次数: `392`
- 验证通过次数: `392`
- rejected: `0`
- Evidence blocker: `0`
- unknown Evidence: `0`
- `ev-629`: `0`
- 跨 chunk / 跨 conversion 绑定: `0`
- LLM 自行创建 Evidence ID: `0`

所有成功 chunk 只返回 `selected_evidence_indexes`；系统完成 index → Evidence 对象 → scope/hash validation。

## Knowledge

- Concept Candidate 数量: `293`
- Relation Candidate 数量: `0`
- Skill Candidate 数量: `0`
- 碎片 Concept 拒绝数量: `12`
- 未解决 Concept 冲突: `11`
- 正式 Concept / Relation / Skill 写入: `0`
- Graph identity collision: `0`

## Vault

- 是否写入正式资产: `否`
- 是否完整: `是；运行诊断完整，正式资产保持零写入`
- 是否回滚: `无需事务回滚；在 commit 前 fail closed`
- Graph 写入: `否`
- Registry 写入: `否`
- Skill 写入: `否`
- Asset Ledger 写入: `否`
- 真实 Vault 是否打开: `否`
- 临时 Vault: `运行结束后删除`

## Final Result

`V3.2.1_READY`

说明：以上结论仅代表真实书籍 Evidence Stability 回归通过。正式发布门禁当前仍因缺少可核验的 GitHub 六环境 CI 汇总证据而阻断，不能据此宣称 `V3.2.1_READY_FOR_PRODUCTION`。
