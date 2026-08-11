from __future__ import annotations

import json
import re
from pathlib import Path

from .evidence import EvidenceCatalog
from .models import ValidationReport
from .utils import parse_frontmatter, sha256_file


class SkillValidator:
    REQUIRED_SECTIONS = (
        "## 1. Skill定位", "## 2. 适用场景", "## 3. 输入要求", "## 4. 判断流程",
        "## 5. 执行动作", "## 6. 输出格式", "## 7. 使用边界", "## 8. 禁止场景", "## 9. 原文证据",
    )
    EVIDENCE_PATTERN = re.compile(r"\bev-[0-9a-f]{64}\b")

    def validate_skill_text(self, text: str, path: str = "SKILL.md", source_text: str | None = None, catalog: EvidenceCatalog | None = None) -> ValidationReport:
        report = ValidationReport()
        frontmatter, body = parse_frontmatter(text)
        keys = set(frontmatter)
        if keys != {"name", "description"}:
            report.add("error", "skill-frontmatter-fields", "Codex Skill frontmatter 必须且只能包含 name 与 description。", path)
        name = str(frontmatter.get("name", ""))
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
            report.add("error", "skill-name", "Skill name 必须是最多 64 字符的 lowercase hyphen-case。", path)
        description = str(frontmatter.get("description", ""))
        if len(description) < 40 or len(description) > 500:
            report.add("error", "skill-description-length", "Skill description 长度必须为 40–500。", path)
        if not re.search(r"(?:use when|何时|当.+时)", description, flags=re.IGNORECASE):
            report.add("error", "skill-description-when", "description 必须说明何时调用。", path)
        if not re.search(r"(?:do not use|不要|禁止|不适用)", description, flags=re.IGNORECASE):
            report.add("error", "skill-description-not", "description 必须说明何时不调用。", path)
        positions = []
        for section in self.REQUIRED_SECTIONS:
            position = body.find(section)
            if position < 0:
                report.add("error", "skill-section", f"缺少必需章节：{section}", path)
            positions.append(position)
        if all(item >= 0 for item in positions) and positions != sorted(positions):
            report.add("error", "skill-section-order", "九个 Skill 章节顺序错误。", path)
        decision = self._section(body, "## 4. 判断流程", "## 5. 执行动作")
        actions = self._section(body, "## 5. 执行动作", "## 6. 输出格式")
        if len(re.findall(r"(?m)^\d+\.\s+", decision)) < 2:
            report.add("error", "skill-decision-count", "判断流程至少两个节点。", path)
        if len(re.findall(r"(?m)^\d+\.\s+", actions)) < 2:
            report.add("error", "skill-action-count", "执行动作至少两个步骤。", path)
        evidence_ids = set(self.EVIDENCE_PATTERN.findall(body))
        if not evidence_ids:
            report.add("error", "skill-evidence", "Skill 没有 V3 Evidence ID。", path)
        if catalog:
            for evidence_id in evidence_ids:
                if catalog.get(evidence_id) is None:
                    report.add("error", "unknown-evidence", f"Skill 引用了 catalog 不存在的 Evidence：{evidence_id}", path)
            report.issues.extend(catalog.verify_integrity().issues)
        for line_number, line in self._content_lines(body):
            if "engine_policy" in line or not line.strip() or line.startswith(("#", "|", ">", "- Skill ID", "- [[", "Engine policy")):
                continue
            if line.startswith(("- ",)) or re.match(r"^\d+\.\s+", line):
                if not self.EVIDENCE_PATTERN.search(line) and "无。" not in line:
                    report.add("warning", "unmarked-statement", "可执行陈述未标注 Evidence 或 engine_policy。", f"{path}:{line_number}")
        return report

    def validate_path(self, path: Path) -> ValidationReport:
        report = ValidationReport()
        if path.is_file():
            if path.name == "SKILL.md":
                return self.validate_skill_text(path.read_text(encoding="utf-8"), str(path))
            if path.suffix.lower() == ".json":
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    report.add("error", "json-invalid", str(exc), str(path))
            return report
        for skill_path in path.rglob("SKILL.md"):
            report.issues.extend(self.validate_skill_text(skill_path.read_text(encoding="utf-8"), str(skill_path)).issues)
        for json_path in path.rglob("*.json"):
            try:
                json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                report.add("error", "json-invalid", str(exc), str(json_path))
        return report

    @staticmethod
    def validate_file_hash(path: Path, expected: str, report: ValidationReport, code: str) -> None:
        if not path.is_file():
            report.add("error", code, f"文件缺失：{path}", str(path))
        elif sha256_file(path) != expected:
            report.add("error", code, f"文件哈希不一致：{path}", str(path))

    @staticmethod
    def _section(body: str, start: str, end: str) -> str:
        start_at = body.find(start)
        end_at = body.find(end, start_at + len(start)) if start_at >= 0 else -1
        return "" if start_at < 0 else body[start_at + len(start):None if end_at < 0 else end_at]

    @staticmethod
    def _content_lines(body: str) -> list[tuple[int, str]]:
        return [(index, line.strip()) for index, line in enumerate(body.splitlines(), 1)]
