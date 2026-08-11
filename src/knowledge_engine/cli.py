from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .exposure import SkillExposureManager
from .graph import ConceptGraph
from .ingest import IngestError
from .integrity import IntegrityReconciler
from .lifecycle import SkillLifecycleService
from .migration import V2MigrationService
from .pipeline import ConversionBlocked, DistillationPipeline, PipelineError
from .providers import AnalyzerProviderError, CommandLLMTransport, HybridAnalyzerProvider, LLMAnalyzerProvider, RuleAnalyzerProvider
from .registry import SkillRegistry
from .synthesis import SynthesisMaterializationService, SynthesisRegistry
from .transaction import TransactionError
from .trigger_eval import TriggerEvalService
from .validator import SkillValidator


def _configure_utf8_io() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowledge-engine", description="Obsidian Knowledge Distillation Engine V3.3 Knowledge Synthesis Layer")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    distill = sub.add_parser("distill", help="不可变摄取、Evidence、知识与 source Skill candidate")
    distill.add_argument("source")
    _add_vault(distill); _add_provider(distill)
    distill.add_argument("--title"); distill.add_argument("--edition-id")
    distill.add_argument("--author", default="Andy"); distill.add_argument("--project", default="建设个人知识库")
    distill.add_argument("--pdf-wrapper", type=Path); distill.add_argument("--url-allow", action="append", default=[])
    distill.add_argument("--update", action="store_true", help="V2 兼容参数；V3 不覆盖不可变资产")
    distill.add_argument("--allow-warnings", action="store_true", help="允许生成 review candidate；不会批准")
    distill.add_argument("--dry-run", action="store_true"); distill.add_argument("--idempotency-key")
    distill.add_argument("--json", action="store_true")

    inspect = sub.add_parser("inspect", help="只运行 Provider 与双层 Gate")
    inspect.add_argument("source"); _add_provider(inspect)
    inspect.add_argument("--title"); inspect.add_argument("--pdf-wrapper", type=Path); inspect.add_argument("--url-allow", action="append", default=[]); inspect.add_argument("--json", action="store_true")

    validate = sub.add_parser("validate", help="校验 Skill/目录结构")
    validate.add_argument("path", type=Path); validate.add_argument("--json", action="store_true")
    doctor = sub.add_parser("doctor", help="检查 Vault、Git、依赖与 PDF wrapper")
    _add_vault(doctor); doctor.add_argument("--pdf-wrapper", type=Path); doctor.add_argument("--json", action="store_true")
    registry = sub.add_parser("registry", help="查看 V3 versioned Skill Registry")
    _add_vault(registry); registry.add_argument("--json", action="store_true")
    graph = sub.add_parser("graph", help="查看治理后的 Concept Graph")
    _add_vault(graph); graph.add_argument("--json", action="store_true")

    for name in ("integrity", "reconcile", "repair"):
        command = sub.add_parser(name, help="全链哈希与交叉核算" if name != "repair" else "WAL 恢复后全链核算")
        _add_vault(command); command.add_argument("--json", action="store_true")

    link = sub.add_parser("link-project", help="建立 version-specific Skill → Project")
    link.add_argument("skill"); link.add_argument("--version"); link.add_argument("--project", required=True); link.add_argument("--idempotency-key"); _add_vault(link); link.add_argument("--json", action="store_true")
    use = sub.add_parser("record-use", help="记录 version-specific invocation/outcome/feedback")
    use.add_argument("skill"); use.add_argument("--version"); use.add_argument("--project", required=True)
    use.add_argument("--outcome", required=True, choices=("success", "partial", "failed")); use.add_argument("--feedback")
    use.add_argument("--idempotency-key"); _add_vault(use); use.add_argument("--json", action="store_true")

    skill = sub.add_parser("skill", help="Skill lifecycle 与 Codex exposure")
    skill_sub = skill.add_subparsers(dest="skill_command", required=True)
    expose = skill_sub.add_parser("expose"); expose.add_argument("skill"); expose.add_argument("--scope", choices=("user", "repo"), required=True); expose.add_argument("--repo", type=Path); expose.add_argument("--idempotency-key"); _add_vault(expose); expose.add_argument("--json", action="store_true")
    unexpose = skill_sub.add_parser("unexpose"); unexpose.add_argument("skill"); unexpose.add_argument("--idempotency-key"); _add_vault(unexpose); unexpose.add_argument("--json", action="store_true")
    status = skill_sub.add_parser("status"); status.add_argument("skill"); _add_vault(status); status.add_argument("--json", action="store_true")
    reconcile = skill_sub.add_parser("reconcile"); _add_vault(reconcile); reconcile.add_argument("--repair", action="store_true"); reconcile.add_argument("--json", action="store_true")
    generate_cases = skill_sub.add_parser("generate-trigger-cases"); generate_cases.add_argument("skill"); generate_cases.add_argument("--version"); _add_vault(generate_cases); generate_cases.add_argument("--json", action="store_true")
    run_eval = skill_sub.add_parser("run-trigger-eval"); run_eval.add_argument("skill"); run_eval.add_argument("--version", required=True); run_eval.add_argument("--cases-json", type=Path, required=True); run_eval.add_argument("--model", default="default"); run_eval.add_argument("--timeout", type=int, default=240); run_eval.add_argument("--codex-command", default="codex"); run_eval.add_argument("--concurrency", type=int, default=4); _add_vault(run_eval); run_eval.add_argument("--json", action="store_true")
    for action in ("approve", "reject", "deprecate"):
        item = skill_sub.add_parser(action); item.add_argument("skill"); item.add_argument("--version", required=True); item.add_argument("--reviewer", required=True); item.add_argument("--reason", required=True); _add_vault(item); item.add_argument("--json", action="store_true")
    rollback = skill_sub.add_parser("rollback"); rollback.add_argument("skill"); rollback.add_argument("--version", required=True); rollback.add_argument("--reviewer", required=True); rollback.add_argument("--reason", required=True); _add_vault(rollback); rollback.add_argument("--json", action="store_true")
    compare = skill_sub.add_parser("compare"); compare.add_argument("skill"); compare.add_argument("--left", required=True); compare.add_argument("--right", required=True); _add_vault(compare); compare.add_argument("--json", action="store_true")
    record_eval = skill_sub.add_parser("record-eval"); record_eval.add_argument("skill"); record_eval.add_argument("--version", required=True); record_eval.add_argument("--eval-json", type=Path, required=True); _add_vault(record_eval); record_eval.add_argument("--json", action="store_true")
    semantic_review = skill_sub.add_parser("semantic-review"); semantic_review.add_argument("skill"); semantic_review.add_argument("--version", required=True); semantic_review.add_argument("--review-json", type=Path, required=True); semantic_review.add_argument("--reviewer", required=True); _add_vault(semantic_review); semantic_review.add_argument("--json", action="store_true")

    synthesis = sub.add_parser("synthesis", help="跨书 proposal 与人工批准物化")
    synth_sub = synthesis.add_subparsers(dest="synthesis_command", required=True)
    synth_list = synth_sub.add_parser("list"); _add_vault(synth_list); synth_list.add_argument("--json", action="store_true")
    synth_materialize = synth_sub.add_parser("materialize"); synth_materialize.add_argument("proposal_id"); synth_materialize.add_argument("--approver", required=True); synth_materialize.add_argument("--reason", required=True); synth_materialize.add_argument("--candidate-json", type=Path, required=True); synth_materialize.add_argument("--name"); _add_vault(synth_materialize); synth_materialize.add_argument("--json", action="store_true")

    migrate = sub.add_parser("migrate-v2", help="非破坏 V2 dry-run/apply/rollback")
    _add_vault(migrate); mode = migrate.add_mutually_exclusive_group(required=True); mode.add_argument("--dry-run", action="store_true"); mode.add_argument("--apply", action="store_true"); mode.add_argument("--rollback"); migrate.add_argument("--json", action="store_true")
    return parser


def _add_vault(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vault", type=Path, default=os.environ.get("OBSIDIAN_VAULT"), help="现有 Obsidian Vault；V3 不创建第二知识库")


def _add_provider(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--analyzer", choices=("rule", "llm", "hybrid"), default="rule")
    parser.add_argument("--llm-command"); parser.add_argument("--llm-timeout", type=int, default=180); parser.add_argument("--llm-model", default="external-command")


def _require_vault(value: Path | str | None) -> Path:
    if not value:
        raise PipelineError("必须指定现有 Vault；V3 不创建第二套知识库。")
    return Path(value).resolve()


def _provider(args: argparse.Namespace):
    if args.analyzer == "rule":
        return RuleAnalyzerProvider()
    if not args.llm_command:
        raise PipelineError("选择 llm/hybrid 时必须提供 --llm-command。")
    transport = CommandLLMTransport.from_string(args.llm_command, args.llm_timeout)
    transport.model = args.llm_model
    llm = LLMAnalyzerProvider(transport)
    return llm if args.analyzer == "llm" else HybridAnalyzerProvider(llm)


def _emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"状态：{payload.get('status', 'ok')}")
        for key in ("title", "source_id", "conversion_id", "skill", "version", "project", "action", "migration_id", "event_id"):
            if payload.get(key) is not None:
                print(f"{key}: {payload[key]}")


def _handle(args: argparse.Namespace) -> int:
    if args.command == "distill":
        result = DistillationPipeline(_require_vault(args.vault), args.author, args.project, args.pdf_wrapper, _provider(args), tuple(args.url_allow)).distill(args.source, args.title, args.update, args.allow_warnings, args.dry_run, args.edition_id, args.idempotency_key)
        _emit(result.as_dict(), args.json); return 0
    if args.command == "inspect":
        result = DistillationPipeline(Path.cwd(), pdf_wrapper=args.pdf_wrapper, provider=_provider(args), url_allowlist=tuple(args.url_allow)).inspect(args.source, args.title)
        payload = {"status": "passed" if result.validation.passed else "failed", "title": result.analysis.title, "source_id": result.source.source_id, "conversion_id": result.conversion.conversion_id, "provider": result.provider_result.provider, "issues": [item.__dict__ for item in result.validation.issues]}
        _emit(payload, args.json); return 0 if result.validation.passed else 1
    if args.command == "validate":
        report = SkillValidator().validate_path(args.path.resolve()); payload = {"status": "passed" if report.passed else "failed", "issues": [item.__dict__ for item in report.issues]}; _emit(payload, args.json); return 0 if report.passed else 1
    if args.command == "doctor":
        vault = _require_vault(args.vault); wrapper = args.pdf_wrapper or Path(os.environ.get("BOOK_DISTILLER_PDF_WRAPPER", Path.home() / ".codex/tools/markitdown/pdf-to-md.ps1"))
        checks = {"vault_exists": vault.is_dir(), "knowledge_dir": (vault / "05_Knowledge").is_dir(), "sources_dir": (vault / "06_Sources").is_dir(), "git_worktree": (vault / ".git").exists(), "pdf_markitdown_wrapper": wrapper.is_file()}
        payload = {"status": "passed" if all(checks.values()) else "failed", "vault": str(vault), "checks": checks}; _emit(payload, args.json); return 0 if payload["status"] == "passed" else 1
    if args.command == "registry":
        registry = SkillRegistry.load(_require_vault(args.vault)); payload = {"status": "ok", "skills": registry.records, "count": len(registry.records)}; _emit(payload, args.json); return 0
    if args.command == "graph":
        graph = ConceptGraph.load(_require_vault(args.vault)); payload = {"status": "ok", "nodes": graph.nodes, "edges": graph.edges}; _emit(payload, args.json); return 0
    if args.command in {"integrity", "reconcile", "repair"}:
        result = IntegrityReconciler(_require_vault(args.vault)).reconcile(repair=args.command == "repair"); _emit(result.as_dict(), args.json); return 0 if result.passed else 1
    if args.command in {"link-project", "record-use"}:
        service = SkillLifecycleService(_require_vault(args.vault))
        result = service.link_project(args.skill, args.project, args.version, args.idempotency_key) if args.command == "link-project" else service.record_use(args.skill, args.project, args.outcome, args.feedback, args.version, args.idempotency_key)
        payload = {"status": "duplicate" if result.duplicate else "completed", "action": result.action, "skill": result.skill_name, "version": result.version, "project": result.project, "event_id": result.event_id}; _emit(payload, args.json); return 0
    if args.command == "skill":
        return _handle_skill(args)
    if args.command == "synthesis":
        vault = _require_vault(args.vault)
        if args.synthesis_command == "list":
            data = SynthesisRegistry.load(vault); _emit({"status": "ok", "proposals": data.proposals}, args.json); return 0
        candidate = json.loads(args.candidate_json.read_text(encoding="utf-8")); payload = SynthesisMaterializationService(vault).materialize(args.proposal_id, args.approver, args.reason, candidate, args.name); _emit(payload, args.json); return 0
    if args.command == "migrate-v2":
        service = V2MigrationService(_require_vault(args.vault)); result = service.dry_run() if args.dry_run else service.apply() if args.apply else service.rollback(args.rollback); _emit(result.as_dict(), args.json); return 0
    raise PipelineError(f"未知命令：{args.command}")


def _handle_skill(args: argparse.Namespace) -> int:
    vault = _require_vault(args.vault)
    if args.skill_command in {"expose", "unexpose", "status", "reconcile"}:
        manager = SkillExposureManager(vault)
        if args.skill_command == "expose": result = manager.expose(args.skill, args.scope, args.repo, args.idempotency_key).as_dict()
        elif args.skill_command == "unexpose": result = manager.unexpose(args.skill, args.idempotency_key).as_dict()
        elif args.skill_command == "status": result = manager.status(args.skill).as_dict()
        elif args.skill_command == "reconcile": result = manager.reconcile(args.repair)
        _emit(result, args.json); return 0 if result.get("status") not in {"invalid", "issues"} else 1
    if args.skill_command == "generate-trigger-cases":
        path = TriggerEvalService(vault).generate_cases(args.skill, args.version)
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = {"status": "completed", "skill": args.skill, "version": payload["version"], "cases_path": str(path), "case_set_sha256": payload["case_set_sha256"]}
        _emit(result, args.json); return 0
    if args.skill_command == "run-trigger-eval":
        run = TriggerEvalService(vault).run_codex(args.skill, args.version, args.cases_json, args.model, args.timeout, args.codex_command, args.concurrency)
        result = {"status": run.status, "skill": args.skill, "version": args.version, "run_id": run.run_id, "evaluation_path": str(run.run_path), "passed": run.passed}
        _emit(result, args.json); return 0 if run.passed else 1
    service = SkillLifecycleService(vault)
    if args.skill_command == "approve": result = service.approve(args.skill, args.version, args.reviewer, args.reason)
    elif args.skill_command == "reject": result = service.reject(args.skill, args.version, args.reviewer, args.reason)
    elif args.skill_command == "deprecate": result = service.deprecate(args.skill, args.version, args.reviewer, args.reason)
    elif args.skill_command == "rollback": result = service.rollback(args.skill, args.version, args.reviewer, args.reason)
    elif args.skill_command == "compare":
        diff = service.compare(args.skill, args.left, args.right); payload = {"status": "ok", "skill": args.skill, "left": args.left, "right": args.right, "diff": diff}; _emit(payload, args.json); return 0
    elif args.skill_command == "record-eval":
        payload = json.loads(args.eval_json.read_text(encoding="utf-8")); result = service.record_eval(args.skill, args.version, payload, args.eval_json)
    elif args.skill_command == "semantic-review":
        review = json.loads(args.review_json.read_text(encoding="utf-8")); result = service.semantic_review(args.skill, args.version, review, args.reviewer)
    else: raise PipelineError(f"未知 skill 命令：{args.skill_command}")
    payload = {"status": "completed", "action": result.action, "skill": result.skill_name, "version": result.version, "event_id": result.event_id}; _emit(payload, args.json); return 0


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_io()
    try:
        return _handle(_parser().parse_args(argv))
    except ConversionBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        for path in exc.written_paths: print(f"  archived: {path}", file=sys.stderr)
        return 2
    except (PipelineError, IngestError, AnalyzerProviderError, TransactionError, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr); return 130


if __name__ == "__main__":
    raise SystemExit(main())
