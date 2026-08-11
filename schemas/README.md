# V3 JSON Schemas

V3.2 adds `source-identity.schema.json` and extends conversion, Evidence, manifest, Skill-version, and asset-ledger schemas without invalidating V3.1 metadata. New Evidence records carry both normalized and conversion locators.

- `concept-graph.schema.json`：治理图的顶层、node 与 edge 契约。
- `llm-candidate.schema.json`：LLM 候选输出的大小、字段和结构契约。
- `pdf-metadata.schema.json`：本地 Microsoft MarkItDown wrapper 的 metadata 契约。

这些文件由 `python scripts/export_schemas.py` 从运行时代码导出；正式验收会重新导出，避免文档与执行逻辑漂移。
