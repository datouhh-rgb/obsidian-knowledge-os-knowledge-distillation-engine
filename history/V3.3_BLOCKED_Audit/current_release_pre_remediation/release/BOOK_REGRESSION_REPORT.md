# V3.3.0 Real Book Regression Report

## 《股票大作手回忆录》

- Run ID: `run-0612616f2d21999e726a3ba1`
- Source ID: `75dba888e796b4aae0ef85f99798b6c8eab002e2c2b213e98b9b3e5d5c32b828`
- Conversion ID: `d563d127872852958eb4b6e311258407b5865229e0eb52342963c113ad0a25e3`
- Analyzer: `HybridAnalyzerProvider(LLMAnalyzerProvider)` / `gpt-5.6-terra`
- Chunks: `42/42`, failed `0`
- Portable Evidence selection: PASS
- Concept candidates observed: `289`
- Method Clusters: `5`
- Skill Candidates: `3`, all `candidate`
- Graph/Registry/Asset Ledger: committed in the disposable regression Vault
- Graph identity collision: none
- Automatic approval/exposure/project connection: none
- Production Vault opened: no

Result: `PASS`

## 《认知觉醒》

- Run ID: `run-0762d8c86ba886ba8bcba322`
- Source ID: `db48eecb044ede32061660722051a87540cf8f8cb907b2fde860719141ff2734`
- Conversion ID: `044729267958dc4755a3bf33a67bb16f5a667079422a62d092d1ba7f2963cf73`
- Analyzer: `HybridAnalyzerProvider(LLMAnalyzerProvider)` / `gpt-5.6-terra`
- Chunks: `86/86`
- Portable Evidence selection: PASS (`79` payloads, `49/49` unique extraction hashes)
- Unknown Evidence: none
- Blocker: `candidate-title` — a Concept candidate title was empty, too short, or too long
- Evidence/Concept/Method/Skill committed: `0/0/0/0`
- Graph/Registry/Asset Ledger committed: no
- Automatic approval/exposure/project connection: none
- Production Vault opened: no

The unchanged quality gate rejected the run after extraction, and the Vault transaction failed closed without partial formal knowledge assets.

Result: `BLOCKED (fail closed)`

## PDF conversion record

- Source: `C:\Users\ThinkPad\Documents\知识库\Andy-PKB\90_Attachments\PDF\认知觉醒：开启自我改变的原动力+(周岭)+(Z-Library).pdf`
- Markdown: `C:\Users\ThinkPad\AppData\Local\Temp\codex-markitdown\认知觉醒：开启自我改变的原动力+(周岭)+(Z-Library).db48eecb044e.md`
- Converter: Microsoft MarkItDown `0.1.7`
- Python: `3.12.10`
- Status: `ok`; warnings: none; cache hit: false; fallback: none
- Original page visual verification: not performed; no layout, table, or page-number claims are made

## Combined release result

`V3.3.0_BLOCKED`

