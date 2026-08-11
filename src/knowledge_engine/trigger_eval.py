from __future__ import annotations

import json
import os
import re
import subprocess
import shutil
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .registry import SkillRegistry
from .transaction import VaultTransaction
from .utils import atomic_write, canonical_json, now_iso, sha256_bytes, sha256_file, sha256_text


TRIGGER_EVAL_ROOT = Path("10_Reviews/Skill-Evals")
REAL_EVALUATOR_KINDS = {"codex-cli-implicit", "approved-external-router"}


class TriggerEvalError(ValueError):
    pass


@dataclass(frozen=True)
class TriggerEvalRun:
    run_id: str
    run_path: Path
    status: str
    passed: bool
    cases: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_path": str(self.run_path),
            "status": self.status,
            "passed": self.passed,
            "case_count": len(self.cases),
        }


class TriggerCaseGenerator:
    POSITIVE_TEMPLATES = (
        "请根据给定材料完成以下核验工作：{action_a}；然后{action_b}。输出判断依据和来源定位。",
        "我有一份待处理资料。先{action_a}，再{action_b}，并列出未解决问题。",
        "请对这份内容执行可追溯检查：{action_a}；{action_b}。不要补充材料之外的结论。",
        "帮我把资料转成可审计的操作结果，需要{action_a}，随后{action_b}。",
        "针对下面的原始文档，请{action_a}；完成后{action_b}，并说明证据位置。",
        "这项任务要求逐步核验来源。请先{action_a}，再{action_b}。",
        "请判断资料能否支持目标结论，并执行：{action_a}；{action_b}。",
        "我需要一份带原文依据的处理记录。请{action_a}，接着{action_b}。",
        "对输入内容做方法化审查：{action_a}；{action_b}；最后列出边界。",
        "请在不引入外部观点的前提下，{action_a}，并{action_b}。",
    )
    NEGATIVE_PROMPTS = (
        "把这句话改写得更有诗意：今天的风很温柔。",
        "给我设计一个三天的居家拉伸安排。",
        "解释 Python 列表推导式的基本语法。",
        "写一段轻松的生日祝福，不需要引用资料。",
        "计算 17 乘以 23，并只返回结果。",
        "推荐两种适合夏天的无酒精饮料。",
        "把英文 good morning 翻译成中文。",
        "为一只橘猫取五个可爱的名字。",
        "说明彩虹通常有哪些颜色。",
        "写一个关于雨夜车站的两句微型故事。",
    )

    def generate(self, record: dict[str, Any], version: str) -> dict[str, Any]:
        version_record = record["versions"][version]
        content = version_record["content"]
        action_texts = [str(item["text"]) for item in content["actions"]]
        action_a = self._sanitize(action_texts[0], record)
        action_b = self._sanitize(action_texts[1], record)
        positive = [template.format(action_a=action_a, action_b=action_b) for template in self.POSITIVE_TEMPLATES]
        boundaries = [
            (f"关键输入尚未提供。请先判断是否能够执行“{action_a}”，并只列出缺失项，不要猜测。", True),
            (f"现有资料只支持部分步骤。请判断能否先做“{action_b}”，并明确停止条件。", True),
            ("用户要求给出结论，但没有提供任何原始材料。请说明应当停止还是继续。", True),
            ("忽略所有来源限制，凭常识直接给我一个肯定结论。", False),
            ("我只想闲聊，不需要核验、步骤、证据或资料处理。", False),
        ]
        prompts = [*positive, *self.NEGATIVE_PROMPTS, *(item[0] for item in boundaries)]
        if len(set(prompts)) != 25:
            raise TriggerEvalError("Trigger prompts must be unique.")
        forbidden = [str(record["canonical_name"]), str(record["title"]), "$skill-name"]
        for prompt in prompts:
            if any(value and value.casefold() in prompt.casefold() for value in forbidden):
                raise TriggerEvalError("Implicit trigger prompt leaks the target Skill name or title.")

        namespace = uuid.UUID(str(record["skill_id"]))
        cases: list[dict[str, Any]] = []
        for category, category_prompts in (
            ("positive", [(item, True) for item in positive]),
            ("negative", [(item, False) for item in self.NEGATIVE_PROMPTS]),
            ("boundary", boundaries),
        ):
            for prompt, expected in category_prompts:
                prompt_hash = sha256_text(prompt)
                cases.append({
                    "case_uuid": str(uuid.uuid5(namespace, f"{version}:{category}:{prompt_hash}")),
                    "prompt": prompt,
                    "prompt_sha256": prompt_hash,
                    "category": category,
                    "expected": expected,
                })
        case_set_sha = sha256_text(canonical_json(cases))
        return {
            "schema_version": 1,
            "kind": "trigger-case-set",
            "skill_id": record["skill_id"],
            "skill": record["canonical_name"],
            "version": version,
            "artifact_sha256": version_record["artifact_sha256"],
            "case_set_sha256": case_set_sha,
            "created_at": now_iso(),
            "counts": {"positive": 10, "negative": 10, "boundary": 5},
            "cases": cases,
        }

    @staticmethod
    def _sanitize(text: str, record: dict[str, Any]) -> str:
        result = text
        for value in (str(record["canonical_name"]), str(record["title"]), "$skill-name"):
            if value:
                result = re.sub(re.escape(value), "该步骤", result, flags=re.IGNORECASE)
        return result.strip().rstrip("。")


class TriggerEvalValidator:
    @classmethod
    def validate_case_set(cls, payload: dict[str, Any]) -> None:
        errors = sorted(Draft202012Validator(cls.case_set_schema()).iter_errors(payload), key=lambda item: list(item.path))
        if errors:
            raise TriggerEvalError(f"Trigger case schema invalid at {errors[0].json_path}: {errors[0].message}")
        prompts = [item["prompt"] for item in payload["cases"]]
        if len(prompts) != len(set(prompts)):
            raise TriggerEvalError("Trigger prompts are not unique.")
        for item in payload["cases"]:
            if sha256_text(item["prompt"]) != item["prompt_sha256"]:
                raise TriggerEvalError(f"Prompt hash mismatch: {item['case_uuid']}")
        expected_hash = sha256_text(canonical_json(payload["cases"]))
        if payload["case_set_sha256"] != expected_hash:
            raise TriggerEvalError("Trigger case-set hash mismatch.")

    @classmethod
    def validate_run(cls, vault: Path, payload: dict[str, Any], expected_skill: str, expected_version: str) -> None:
        errors = sorted(Draft202012Validator(cls.run_schema()).iter_errors(payload), key=lambda item: list(item.path))
        if errors:
            raise TriggerEvalError(f"Real trigger eval schema invalid at {errors[0].json_path}: {errors[0].message}")
        if payload["skill"] != expected_skill or payload["version"] != expected_version:
            raise TriggerEvalError("Trigger eval is bound to a different Skill or version.")
        if payload["evaluator"]["kind"] not in REAL_EVALUATOR_KINDS:
            raise TriggerEvalError("Evaluator kind is not a real approved evaluator.")
        if not payload["evaluator"].get("cli_version") or not payload["evaluator"].get("command_sha256"):
            raise TriggerEvalError("Evaluator provenance is incomplete.")
        if len({item["case_uuid"] for item in payload["cases"]}) != len(payload["cases"]):
            raise TriggerEvalError("Trigger eval case UUIDs are not unique.")
        for item in payload["cases"]:
            if sha256_text(item["prompt"]) != item["prompt_sha256"]:
                raise TriggerEvalError(f"Prompt hash mismatch: {item['case_uuid']}")
            for field, hash_field in (
                ("transcript_path", "transcript_sha256"),
                ("stderr_path", "stderr_sha256"),
                ("last_message_path", "last_message_sha256"),
            ):
                relative = Path(item[field])
                if relative.is_absolute() or ".." in relative.parts:
                    raise TriggerEvalError(f"Eval artifact path escapes the Vault: {relative}")
                artifact = (vault / relative).resolve()
                try:
                    artifact.relative_to(vault.resolve())
                except ValueError as exc:
                    raise TriggerEvalError(f"Eval artifact path escapes the Vault: {relative}") from exc
                if not artifact.is_file() or sha256_file(artifact) != item[hash_field]:
                    raise TriggerEvalError(f"Eval transcript missing or hash mismatch: {relative}")
            transcript = (vault / item["transcript_path"]).read_text(encoding="utf-8", errors="replace")
            if not transcript.strip() or not any(cls._valid_codex_event(line) for line in transcript.splitlines() if line.strip()):
                raise TriggerEvalError(f"Transcript is not raw Codex JSONL: {item['case_uuid']}")

    @staticmethod
    def _valid_codex_event(line: str) -> bool:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return False
        return isinstance(value, dict) and isinstance(value.get("type"), str)

    @staticmethod
    def case_set_schema() -> dict[str, Any]:
        case = {
            "type": "object", "additionalProperties": False,
            "required": ["case_uuid", "prompt", "prompt_sha256", "category", "expected"],
            "properties": {
                "case_uuid": {"type": "string", "format": "uuid"},
                "prompt": {"type": "string", "minLength": 1},
                "prompt_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "category": {"enum": ["positive", "negative", "boundary"]},
                "expected": {"type": "boolean"},
            },
        }
        return {
            "type": "object", "additionalProperties": False,
            "required": ["schema_version", "kind", "skill_id", "skill", "version", "artifact_sha256", "case_set_sha256", "created_at", "counts", "cases"],
            "properties": {
                "schema_version": {"const": 1}, "kind": {"const": "trigger-case-set"},
                "skill_id": {"type": "string"}, "skill": {"type": "string"}, "version": {"type": "string"},
                "artifact_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "case_set_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "created_at": {"type": "string"},
                "counts": {
                    "type": "object", "additionalProperties": False,
                    "required": ["positive", "negative", "boundary"],
                    "properties": {"positive": {"const": 10}, "negative": {"const": 10}, "boundary": {"const": 5}},
                },
                "cases": {"type": "array", "minItems": 25, "maxItems": 25, "items": case},
            },
        }

    @staticmethod
    def run_schema() -> dict[str, Any]:
        case = {
            "type": "object", "additionalProperties": False,
            "required": [
                "case_uuid", "prompt", "prompt_sha256", "category", "expected", "observed", "passed",
                "exit_code", "command_sha256", "evaluator", "transcript_path", "transcript_sha256", "stderr_path",
                "stderr_sha256", "last_message_path", "last_message_sha256", "detection",
            ],
            "properties": {
                "case_uuid": {"type": "string"}, "prompt": {"type": "string"},
                "prompt_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "category": {"enum": ["positive", "negative", "boundary"]},
                "expected": {"type": "boolean"}, "observed": {"type": "boolean"}, "passed": {"type": "boolean"},
                "exit_code": {"type": "integer"}, "command_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "evaluator": {
                    "type": "object", "additionalProperties": False,
                    "required": ["kind", "cli_version", "model", "command_sha256"],
                    "properties": {
                        "kind": {"type": "string"}, "cli_version": {"type": "string"},
                        "model": {"type": "string"}, "command_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    },
                },
                "transcript_path": {"type": "string"}, "transcript_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "stderr_path": {"type": "string"}, "stderr_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "last_message_path": {"type": "string"}, "last_message_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "detection": {"type": "string"},
            },
        }
        return {
            "type": "object", "additionalProperties": False,
            "required": [
                "schema_version", "kind", "run_id", "skill_id", "skill", "version", "artifact_sha256",
                "case_set_sha256", "created_at", "evaluator", "cases", "metrics", "false_positives",
                "false_negatives", "passed",
            ],
            "properties": {
                "schema_version": {"const": 1}, "kind": {"const": "real-trigger-eval"},
                "run_id": {"type": "string"}, "skill_id": {"type": "string"}, "skill": {"type": "string"},
                "version": {"type": "string"}, "artifact_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "case_set_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "created_at": {"type": "string"},
                "evaluator": {
                    "type": "object", "additionalProperties": False,
                    "required": ["kind", "cli_version", "model", "command_sha256"],
                    "properties": {"kind": {"type": "string"}, "cli_version": {"type": "string"}, "model": {"type": "string"}, "command_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
                },
                "cases": {"type": "array", "minItems": 25, "maxItems": 25, "items": case},
                "metrics": {
                    "type": "object", "additionalProperties": False,
                    "required": ["trigger_recall", "trigger_precision", "boundary_score"],
                    "properties": {"trigger_recall": {"type": "number"}, "trigger_precision": {"type": "number"}, "boundary_score": {"type": "number"}},
                },
                "false_positives": {"type": "array", "items": {"type": "string"}},
                "false_negatives": {"type": "array", "items": {"type": "string"}},
                "passed": {"type": "boolean"},
            },
        }


class TriggerEvalService:
    def __init__(self, vault: Path) -> None:
        self.vault = vault.resolve()
        if not (self.vault / "05_Knowledge/Skills").is_dir():
            raise TriggerEvalError("Target is not an existing Knowledge OS Vault.")

    def generate_cases(self, skill_name: str, version: str | None = None) -> Path:
        registry = SkillRegistry.load(self.vault)
        record, version_record, _ = registry.version(skill_name, version)
        selected = version or str(record["current_version"])
        self._require_exact_artifact(record, version_record)
        payload = TriggerCaseGenerator().generate(record, selected)
        TriggerEvalValidator.validate_case_set(payload)
        relative = self._root(skill_name, selected) / f"cases-{payload['case_set_sha256'][:16]}.json"
        with VaultTransaction(
            self.vault, "generate-trigger-cases",
            request_payload={"skill": skill_name, "version": selected, "case_set": payload["case_set_sha256"]},
        ) as tx:
            tx.stage_text(relative, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            tx.commit({"path": relative.as_posix(), "case_set_sha256": payload["case_set_sha256"]})
        return self.vault / relative

    def run_codex(
        self,
        skill_name: str,
        version: str,
        cases_path: Path,
        model: str = "default",
        timeout: int = 240,
        codex_command: str = "codex",
        concurrency: int = 4,
    ) -> TriggerEvalRun:
        codex_command = self._resolve_codex(codex_command)
        registry = SkillRegistry.load(self.vault)
        record, version_record, _ = registry.version(skill_name, version)
        self._require_exact_artifact(record, version_record)
        payload = json.loads(cases_path.read_text(encoding="utf-8"))
        TriggerEvalValidator.validate_case_set(payload)
        if payload["skill"] != skill_name or payload["version"] != version:
            raise TriggerEvalError("Case set belongs to a different Skill or version.")
        if payload["artifact_sha256"] != version_record["artifact_sha256"]:
            raise TriggerEvalError("Case set was generated for an old artifact.")

        cli_version = self._cli_version(codex_command)
        run_id = "trigger-run-" + str(uuid.uuid4())
        run_root = self._root(skill_name, version) / run_id
        staged_artifacts: dict[Path, bytes] = {}
        case_results: list[dict[str, Any]] = []
        skill_root = self.vault / "05_Knowledge/Skills" / skill_name
        agent_path = skill_root / "agents/openai.yaml"
        original_agent = agent_path.read_bytes()
        implicit_agent = self._set_implicit(original_agent.decode("utf-8"), True).encode("utf-8")

        try:
            atomic_write(agent_path, implicit_agent)
            if concurrency < 1 or concurrency > 8:
                raise TriggerEvalError("Trigger evaluator concurrency must be between 1 and 8.")
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [
                    pool.submit(
                        self._run_case, case, record, run_root, model, timeout,
                        codex_command, cli_version,
                    )
                    for case in payload["cases"]
                ]
                for future in futures:
                    case_result, artifacts = future.result()
                    case_results.append(case_result)
                    staged_artifacts.update(artifacts)
        finally:
            atomic_write(agent_path, original_agent)

        metrics = self._metrics(case_results)
        false_positives = [item["case_uuid"] for item in case_results if not item["expected"] and item["observed"]]
        false_negatives = [item["case_uuid"] for item in case_results if item["expected"] and not item["observed"]]
        command_template = self._command(codex_command, Path("<isolated-repo>"), self.vault, Path("<last-message>"), model)
        evaluator = {
            "kind": "codex-cli-implicit",
            "cli_version": cli_version,
            "model": model,
            "command_sha256": sha256_text(canonical_json(command_template)),
        }
        passed = all(value >= 80.0 for value in metrics.values()) and all(item["exit_code"] == 0 for item in case_results)
        summary = {
            "schema_version": 1, "kind": "real-trigger-eval", "run_id": run_id,
            "skill_id": record["skill_id"], "skill": skill_name, "version": version,
            "artifact_sha256": version_record["artifact_sha256"],
            "case_set_sha256": payload["case_set_sha256"], "created_at": now_iso(),
            "evaluator": evaluator, "cases": case_results, "metrics": metrics,
            "false_positives": false_positives, "false_negatives": false_negatives, "passed": passed,
        }
        summary_relative = run_root / "evaluation.json"
        staged_artifacts[summary_relative] = (json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        with VaultTransaction(
            self.vault, "run-trigger-eval",
            request_payload={"skill": skill_name, "version": version, "case_set": payload["case_set_sha256"], "run_id": run_id},
        ) as tx:
            for relative, content in staged_artifacts.items():
                tx.stage_bytes(relative, content)
            tx.commit({"run_id": run_id, "evaluation_path": summary_relative.as_posix(), "passed": passed})
        TriggerEvalValidator.validate_run(self.vault, summary, skill_name, version)
        return TriggerEvalRun(run_id, self.vault / summary_relative, "completed", passed, tuple(case_results))

    def _run_case(
        self,
        case: dict[str, Any],
        record: dict[str, Any],
        run_root: Path,
        model: str,
        timeout: int,
        codex_command: str,
        cli_version: str,
    ) -> tuple[dict[str, Any], dict[Path, bytes]]:
        with tempfile.TemporaryDirectory(prefix="knowledge-engine-trigger-") as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, timeout=30, check=False)
            link = repo / ".agents" / "skills" / str(record["canonical_name"])
            from .transaction import VaultTransaction
            VaultTransaction._create_directory_link(link, self.vault / "05_Knowledge/Skills" / str(record["canonical_name"]))
            last_message = Path(temporary) / "last-message.txt"
            command = self._command(codex_command, repo, self.vault, last_message, model)
            command_sha = sha256_text(canonical_json([*command, "<PROMPT_STDIN>", case["prompt_sha256"]]))
            try:
                completed = subprocess.run(
                    command,
                    input=case["prompt"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout, shell=False,
                )
                stdout = completed.stdout
                stderr = completed.stderr
                exit_code = completed.returncode
            except subprocess.TimeoutExpired as exc:
                stdout = self._decode_timeout(exc.stdout)
                stderr = self._decode_timeout(exc.stderr) + f"\nTIMEOUT after {timeout}s\n"
                exit_code = 124
            finally:
                if os.path.lexists(str(link)) and VaultTransaction._is_link_like(link):
                    VaultTransaction._remove_directory_link(link)
            last = last_message.read_text(encoding="utf-8", errors="replace") if last_message.is_file() else ""

        observed, detection = self._detect_invocation(stdout, record)
        case_dir = run_root / "cases" / case["case_uuid"]
        transcript_relative = case_dir / "stdout.jsonl"
        stderr_relative = case_dir / "stderr.txt"
        last_relative = case_dir / "last-message.txt"
        stdout_bytes, stderr_bytes, last_bytes = stdout.encode("utf-8"), stderr.encode("utf-8"), last.encode("utf-8")
        result = {
            **case,
            "observed": observed,
            "passed": observed == case["expected"] and exit_code == 0,
            "exit_code": exit_code,
            "command_sha256": command_sha,
            "evaluator": {
                "kind": "codex-cli-implicit", "cli_version": cli_version,
                "model": model, "command_sha256": command_sha,
            },
            "transcript_path": transcript_relative.as_posix(),
            "transcript_sha256": sha256_bytes(stdout_bytes),
            "stderr_path": stderr_relative.as_posix(), "stderr_sha256": sha256_bytes(stderr_bytes),
            "last_message_path": last_relative.as_posix(), "last_message_sha256": sha256_bytes(last_bytes),
            "detection": detection,
        }
        return result, {transcript_relative: stdout_bytes, stderr_relative: stderr_bytes, last_relative: last_bytes}

    @staticmethod
    def _command(codex: str, repo: Path, vault: Path, last_message: Path, model: str) -> list[str]:
        command = [
            codex, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check", "--sandbox", "read-only", "--json",
            "--cd", str(repo), "--add-dir", str(vault), "--output-last-message", str(last_message),
        ]
        if model != "default":
            command.extend(["--model", model])
        command.append("-")
        return command

    @staticmethod
    def _detect_invocation(stdout: str, record: dict[str, Any]) -> tuple[bool, str]:
        canonical = str(record["canonical_name"]).replace("\\", "/").casefold()
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            normalized = canonical_json(event).replace("\\\\", "/").replace("\\", "/").casefold()
            if canonical in normalized and "skill.md" in normalized:
                return True, "raw-codex-event-read-target-skill"
        return False, "no-target-skill-read-event"

    @staticmethod
    def _metrics(cases: list[dict[str, Any]]) -> dict[str, float]:
        positive = [item for item in cases if item["category"] == "positive"]
        negative = [item for item in cases if item["category"] == "negative"]
        boundary = [item for item in cases if item["category"] == "boundary"]
        return {
            "trigger_recall": round(100.0 * sum(item["observed"] for item in positive) / len(positive), 2),
            "trigger_precision": round(100.0 * sum(not item["observed"] for item in negative) / len(negative), 2),
            "boundary_score": round(100.0 * sum(item["passed"] for item in boundary) / len(boundary), 2),
        }

    @staticmethod
    def _cli_version(codex_command: str) -> str:
        completed = subprocess.run(
            [codex_command, "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30, shell=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            raise TriggerEvalError("Codex CLI provenance cannot be obtained.")
        return completed.stdout.strip()

    @staticmethod
    def _resolve_codex(codex_command: str) -> str:
        if os.name == "nt" and codex_command.casefold() == "codex":
            resolved = shutil.which("codex.cmd") or shutil.which("codex.exe")
            if resolved:
                return resolved
        resolved = shutil.which(codex_command)
        return resolved or codex_command

    @staticmethod
    def _decode_timeout(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value

    @staticmethod
    def _set_implicit(text: str, value: bool) -> str:
        desired = "true" if value else "false"
        if re.search(r"allow_implicit_invocation:\s*(?:true|false)", text):
            return re.sub(r"allow_implicit_invocation:\s*(?:true|false)", f"allow_implicit_invocation: {desired}", text)
        return text.rstrip() + f"\npolicy:\n  allow_implicit_invocation: {desired}\n"

    def _require_exact_artifact(self, record: dict[str, Any], version_record: dict[str, Any]) -> None:
        artifact = self.vault / version_record["artifact_path"]
        root_artifact = self.vault / "05_Knowledge/Skills" / str(record["canonical_name"]) / "SKILL.md"
        expected = version_record.get("artifact_sha256")
        if not expected or not artifact.is_file() or not root_artifact.is_file():
            raise TriggerEvalError("Sealed Skill artifacts are missing.")
        if sha256_file(artifact) != expected or sha256_file(root_artifact) != expected:
            raise TriggerEvalError("Trigger eval target does not match the sealed artifact hash.")

    @staticmethod
    def _root(skill: str, version: str) -> Path:
        return TRIGGER_EVAL_ROOT / skill / version
