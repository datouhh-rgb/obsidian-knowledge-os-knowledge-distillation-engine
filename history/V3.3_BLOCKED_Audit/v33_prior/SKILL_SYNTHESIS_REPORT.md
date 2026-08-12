# V3.3 Skill Synthesis Report

## Implemented path

V3.3 implements the requested path:

```text
stable Canonical Concepts
→ multi-signal Method Cluster
→ Evidence-bound Skill Candidate
→ existing production gates
→ human-review-only Registry entry
```

`KnowledgeSynthesizer` and `SkillSynthesizer` are generic engine components. The stock-operator result is not produced by book-title hardcoding or a fixed list of Skill names.

## Contract coverage

- Method aggregation uses semantic similarity, shared Evidence source, chapter context, decision purpose, and action-flow overlap.
- `creation_reason` persists the clustering signals for audit.
- A Method requires at least two Concepts and three unique Evidence records.
- A Skill includes positioning/purpose, trigger scenarios, input requirements, decision flow, Evidence-grounded actions, output format, boundaries, prohibited scenarios, provider provenance, and Evidence binding.
- Existing semantic, Evidence, executability, quality, graph, and production gates are still applied after synthesis.
- A failed synthesized candidate remains blocked and is absent from the Registry and Skill assets.
- Every accepted Skill remains `candidate`; approval and exposure require explicit later human action.

## Required regression tests

The formal test suite contains and passes:

- `test_concept_to_method_cluster`
- `test_method_cluster_evidence_union`
- `test_skill_synthesis_requires_method`
- `test_skill_candidate_quality_gate`
- `test_stock_operator_skill_synthesis_regression`
- `test_skill_not_auto_approve`

An additional end-to-end integration test verifies Method files, `supports`/`generates` graph edges, candidate-only Registry lifecycle, and absence of Agent exposure.

## Real-book outcome

《股票大作手回忆录》 produced five reviewable Method Clusters. Three Skill Candidates passed the unchanged gates:

1. 买进与卖出的风险执行方法
2. 预期与观察的决策分析方法
3. 卖出与买入的执行行动方法

Two further Method-derived candidates were blocked by the existing Skill quality gate. This is expected fail-closed behavior, not a synthesis failure.

The synthesized candidates and real-book evidence were accepted by the final six-environment production gate for commit `70a6898bd288534f4be80bc6bce5837aa4184d84`.

## Current boundary

Synthesis in V3.3 runs inside one distillation transaction and therefore clusters Concepts from the active source/conversion. The plural `source_books` field and graph contract remain compatible with later reviewed aggregation, but this release does not claim automatic cross-book promotion.
