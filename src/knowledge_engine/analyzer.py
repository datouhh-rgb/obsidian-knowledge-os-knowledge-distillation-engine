from __future__ import annotations

import re

from .models import Analysis, Case, Concept, Evidence, Method, Section
from .utils import unique_preserving_order


class BookAnalyzer:
    """Conservative, extractive V1 analyzer.

    It creates assets only from literal source spans. It does not invent missing
    arguments, steps, examples, or applicability claims.
    """

    HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)(.+?)\s*$")
    METHOD_HEADING = re.compile(r"方法|步骤|流程|清单|操作|实践|method|process|workflow|how\s+to|steps", re.I)
    CASE_HEADING = re.compile(r"案例|例子|示例|case|example", re.I)
    CONCEPT_PATTERNS = (
        re.compile(r"^(?:[-*+]\s*)?(?:\*\*|__)?(?P<title>[^：:。.!?]{2,40})(?:\*\*|__)?\s*[：:]\s*(?P<body>.{4,300})$"),
        re.compile(r"^(?:[-*+]\s*)?(?P<title>[^，,。.!?]{2,30}?)(?:是|指|意味着|定义为)\s*(?P<body>.{4,300})$"),
        re.compile(r"^(?:[-*+]\s*)?(?P<title>[A-Za-z][A-Za-z0-9 -]{1,35})\s+(?:is|means|refers to)\s+(?P<body>.{4,300})$", re.I),
    )

    def analyze(self, text: str, title_override: str | None = None) -> Analysis:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.splitlines()
        title = title_override or self._detect_title(lines)
        sections = self._sections(lines, title)
        analysis = Analysis(title=title, sections=sections)
        analysis.chapter_evidence = self._chapter_evidence(sections)
        analysis.concepts = self._concepts(sections)
        analysis.methods = self._methods(sections)
        analysis.cases = self._cases(sections)
        return analysis

    def _detect_title(self, lines: list[str]) -> str:
        for line in lines:
            match = self.HEADING.match(line.strip())
            if match and len(match.group(1)) == 1:
                return self._clean_inline(match.group(2))
        for line in lines:
            if line.strip():
                return self._clean_inline(line.strip())[:80]
        return "未命名资料"

    def _sections(self, lines: list[str], title: str) -> list[Section]:
        headings: list[tuple[int, int, str]] = []
        for index, line in enumerate(lines, start=1):
            match = self.HEADING.match(line.strip())
            if match:
                headings.append((index, len(match.group(1)), self._clean_inline(match.group(2))))
        if not headings:
            return [Section(title=title, level=1, start_line=1, end_line=max(len(lines), 1), lines=tuple(lines))]
        sections: list[Section] = []
        if headings[0][0] > 1:
            sections.append(Section(title="前言", level=1, start_line=1, end_line=headings[0][0] - 1, lines=tuple(lines[:headings[0][0] - 1])))
        for position, (start, level, heading_title) in enumerate(headings):
            end = headings[position + 1][0] - 1 if position + 1 < len(headings) else len(lines)
            sections.append(Section(heading_title, level, start, end, tuple(lines[start - 1:end])))
        return sections

    def _chapter_evidence(self, sections: list[Section]) -> list[Evidence]:
        evidence: list[Evidence] = []
        sequence = 1
        for section in sections:
            if section.level > 2:
                continue
            excerpt, start, end = self._first_substantive_span(section)
            if not excerpt:
                continue
            evidence.append(Evidence(f"chapter-{sequence:03d}", section.title, start, end, excerpt))
            sequence += 1
        return evidence

    def _concepts(self, sections: list[Section]) -> list[Concept]:
        concepts: list[Concept] = []
        seen: set[str] = set()
        sequence = 1
        for section in sections:
            for offset, raw_line in enumerate(section.lines):
                line = self._clean_inline(raw_line.strip())
                if not line or self.HEADING.match(raw_line.strip()) or len(line) > 360:
                    continue
                for pattern in self.CONCEPT_PATTERNS:
                    match = pattern.match(line)
                    if not match:
                        continue
                    concept_title = self._clean_inline(match.group("title")).strip("：:，,。.")
                    body = self._clean_inline(match.group("body"))
                    if self._invalid_concept(concept_title, body) or concept_title in seen:
                        break
                    line_number = section.start_line + offset
                    evidence = Evidence(f"concept-{sequence:03d}", section.title, line_number, line_number, line)
                    concepts.append(Concept(concept_title, line, evidence))
                    seen.add(concept_title)
                    sequence += 1
                    break
        return concepts[:40]

    def _methods(self, sections: list[Section]) -> list[Method]:
        methods: list[Method] = []
        sequence = 1
        for section in sections:
            if not self.METHOD_HEADING.search(section.title):
                continue
            steps: list[str] = []
            evidence: list[Evidence] = []
            for offset, raw_line in enumerate(section.lines):
                match = self.LIST_ITEM.match(raw_line)
                if not match:
                    continue
                step = self._clean_inline(match.group(1))
                if len(step) < 2:
                    continue
                line_number = section.start_line + offset
                steps.append(step)
                evidence.append(Evidence(f"method-{sequence:03d}-step-{len(steps):02d}", section.title, line_number, line_number, self._clean_inline(raw_line.strip())))
            steps = unique_preserving_order(steps)
            if len(steps) >= 2:
                methods.append(Method(section.title, tuple(steps), tuple(evidence[:len(steps)])))
                sequence += 1
        return methods[:20]

    def _cases(self, sections: list[Section]) -> list[Case]:
        cases: list[Case] = []
        sequence = 1
        for section in sections:
            section_is_case = bool(self.CASE_HEADING.search(section.title))
            for offset, raw_line in enumerate(section.lines):
                line = self._clean_inline(raw_line.strip())
                if not line or self.HEADING.match(raw_line.strip()):
                    continue
                if not section_is_case and not re.search(r"例如|比如|举例|for example|e\.g\.", line, re.I):
                    continue
                if len(line) < 8 or len(line) > 500:
                    continue
                line_number = section.start_line + offset
                evidence = Evidence(f"case-{sequence:03d}", section.title, line_number, line_number, line)
                title = section.title if section_is_case else f"{section.title}示例{sequence}"
                cases.append(Case(title, line, evidence))
                sequence += 1
        return cases[:30]

    def _first_substantive_span(self, section: Section) -> tuple[str, int, int]:
        collected: list[str] = []
        start = 0
        end = 0
        for offset, raw_line in enumerate(section.lines):
            stripped = raw_line.strip()
            if not stripped or self.HEADING.match(stripped):
                if collected:
                    break
                continue
            if not collected:
                start = section.start_line + offset
            collected.append(self._clean_inline(stripped))
            end = section.start_line + offset
            if sum(len(item) for item in collected) >= 240 or len(collected) >= 3:
                break
        return " ".join(collected)[:500], start, end

    @staticmethod
    def _clean_inline(value: str) -> str:
        value = re.sub(r"[`*_~]", "", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _invalid_concept(title: str, body: str) -> bool:
        if not (2 <= len(title) <= 40 and 4 <= len(body) <= 300):
            return True
        generic = {"注意", "说明", "例如", "比如", "步骤", "方法", "案例", "example", "note"}
        return title.lower() in generic or title[0].isdigit()

