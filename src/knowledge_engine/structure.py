from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .utils import normalize_text, sha256_text


@dataclass(frozen=True)
class LocatorMapping:
    normalized_line: int
    conversion_start_line: int
    conversion_end_line: int

    @property
    def normalized_locator(self) -> str:
        return f"lines:{self.normalized_line}-{self.normalized_line}"

    @property
    def conversion_locator(self) -> str:
        return f"lines:{self.conversion_start_line}-{self.conversion_end_line}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "normalized_line": self.normalized_line,
            "normalized_locator": self.normalized_locator,
            "conversion_locator": self.conversion_locator,
            "conversion_start_line": self.conversion_start_line,
            "conversion_end_line": self.conversion_end_line,
        }


@dataclass(frozen=True)
class NormalizedDocument:
    text: str
    source_format: str
    normalizer: str
    normalizer_version: str
    conversion_text_sha256: str
    normalized_text_sha256: str
    locator_map: tuple[LocatorMapping, ...]
    table_of_contents: tuple[str, ...]
    hierarchy: tuple[dict[str, Any], ...]
    image_omissions: tuple[str, ...]
    warnings: tuple[str, ...]

    def conversion_locator_for(self, start_line: int, end_line: int) -> str:
        selected = [item for item in self.locator_map if start_line <= item.normalized_line <= end_line]
        if not selected:
            return f"lines:{start_line}-{end_line}"
        return f"lines:{min(item.conversion_start_line for item in selected)}-{max(item.conversion_end_line for item in selected)}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source_format": self.source_format,
            "normalizer": self.normalizer,
            "normalizer_version": self.normalizer_version,
            "conversion_text_sha256": self.conversion_text_sha256,
            "normalized_text_sha256": self.normalized_text_sha256,
            "table_of_contents": list(self.table_of_contents),
            "hierarchy": list(self.hierarchy),
            "image_omissions": list(self.image_omissions),
            "warnings": list(self.warnings),
            "locator_map": [item.as_dict() for item in self.locator_map],
        }


@dataclass
class _MappedLine:
    text: str
    start: int
    end: int


class RealStructureNormalizer:
    """Conservative document cleanup that never invents source prose.

    Every emitted line retains a conversion-line span. The heuristics intentionally
    prefer leaving noise in place over deleting unique content.
    """

    NAME = "real-structure-normalizer"
    VERSION = "1.0.0"
    PAGE_MARKER = "\u0000PAGE_BREAK\u0000"
    PAGE_NUMBER = re.compile(r"^(?:[-–—]?\s*)?(?:第\s*)?\d{1,5}(?:\s*页)?(?:\s*[-–—]?)$")
    TOC_LINE = re.compile(r"^.{2,80}(?:\.{2,}|…{2,}|\s{3,})\s*\d{1,5}\s*$")
    MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    CHAPTER_HEADING = re.compile(
        r"^(?:第[一二三四五六七八九十百千万0-9]+[章节篇部卷]|"
        r"(?:chapter|part)\s+[0-9ivxlcdm]+\b|"
        r"[一二三四五六七八九十百]+[、.．]\s*)",
        re.IGNORECASE,
    )
    IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)|\[IMAGE OMITTED(?::\s*([^\]]+))?\]", re.IGNORECASE)

    def normalize(self, text: str, source_format: str) -> NormalizedDocument:
        conversion_text = normalize_text(text)
        raw_lines = conversion_text.replace("\f", f"\n{self.PAGE_MARKER}\n").splitlines()
        pages = self._pages(raw_lines)
        repeated_margin = self._repeated_margin_lines(pages)
        mapped: list[_MappedLine] = []
        omissions: list[str] = []
        warnings: list[str] = []
        for line_no, raw in enumerate(raw_lines, start=1):
            stripped = raw.strip()
            if stripped == self.PAGE_MARKER:
                mapped.append(_MappedLine(self.PAGE_MARKER, line_no, line_no))
                continue
            margin_key = self._margin_key(stripped)
            if margin_key and margin_key in repeated_margin:
                continue
            if self.PAGE_NUMBER.fullmatch(stripped):
                continue
            image_match = self.IMAGE.search(stripped)
            if image_match:
                reference = (image_match.group(1) or image_match.group(2) or "unidentified image").strip()
                declaration = f"> [IMAGE OMITTED: {reference}]"
                omissions.append(reference)
                stripped = self.IMAGE.sub(declaration, stripped)
            mapped.append(_MappedLine(stripped, line_no, line_no))

        mapped = self._merge_cross_page_headings(mapped)
        mapped = self._restore_lines(mapped)
        mapped = self._recognize_headings(mapped)
        mapped = self._collapse_blanks(mapped)

        output_lines = [item.text for item in mapped if item.text != self.PAGE_MARKER]
        locator_map = tuple(
            LocatorMapping(index, item.start, item.end)
            for index, item in enumerate((item for item in mapped if item.text != self.PAGE_MARKER), start=1)
        )
        normalized = normalize_text("\n".join(output_lines))
        toc = tuple(item.text for item in mapped if self.TOC_LINE.fullmatch(item.text.strip()))
        hierarchy: list[dict[str, Any]] = []
        for index, line in enumerate(output_lines, start=1):
            match = self.MARKDOWN_HEADING.match(line)
            if match:
                hierarchy.append({"title": match.group(2).strip(), "level": len(match.group(1)), "normalized_line": index})
        if omissions:
            warnings.append(f"{len(omissions)} image reference(s) were declared as omitted; no visual claims were inferred.")
        return NormalizedDocument(
            text=normalized,
            source_format=source_format.casefold(),
            normalizer=self.NAME,
            normalizer_version=self.VERSION,
            conversion_text_sha256=sha256_text(conversion_text),
            normalized_text_sha256=sha256_text(normalized),
            locator_map=locator_map,
            table_of_contents=toc,
            hierarchy=tuple(hierarchy),
            image_omissions=tuple(sorted(set(omissions))),
            warnings=tuple(warnings),
        )

    def _pages(self, lines: list[str]) -> list[list[str]]:
        pages: list[list[str]] = [[]]
        for line in lines:
            if line.strip() == self.PAGE_MARKER:
                pages.append([])
            else:
                pages[-1].append(line)
        return pages

    def _repeated_margin_lines(self, pages: list[list[str]]) -> set[str]:
        if len(pages) < 2:
            return set()
        counts: dict[str, int] = {}
        for page in pages:
            nonempty = [line.strip() for line in page if line.strip()]
            candidates = {self._margin_key(line) for line in [*nonempty[:2], *nonempty[-2:]]}
            for key in candidates - {""}:
                counts[key] = counts.get(key, 0) + 1
        threshold = max(2, (len(pages) + 1) // 2)
        return {key for key, count in counts.items() if count >= threshold and len(key) <= 120}

    @staticmethod
    def _margin_key(value: str) -> str:
        if not value:
            return ""
        return re.sub(r"\d+", "#", re.sub(r"\s+", " ", value)).casefold()

    def _merge_cross_page_headings(self, lines: list[_MappedLine]) -> list[_MappedLine]:
        result: list[_MappedLine] = []
        index = 0
        while index < len(lines):
            current = lines[index]
            if current.text == self.PAGE_MARKER and result and index + 1 < len(lines):
                previous = result[-1]
                following = lines[index + 1]
                if self._heading_fragment(previous.text, following.text):
                    result[-1] = _MappedLine(previous.text.rstrip("-— ") + following.text.lstrip(), previous.start, following.end)
                    index += 2
                    continue
                index += 1
                continue
            result.append(current)
            index += 1
        return result

    def _heading_fragment(self, left: str, right: str) -> bool:
        left = left.strip(); right = right.strip()
        if not left or not right or len(left) + len(right) > 100:
            return False
        return bool(self.CHAPTER_HEADING.match(left) or self.MARKDOWN_HEADING.match(left)) and not self._sentence_ended(left)

    def _restore_lines(self, lines: list[_MappedLine]) -> list[_MappedLine]:
        result: list[_MappedLine] = []
        for item in lines:
            if item.text == self.PAGE_MARKER:
                continue
            if not result or not item.text or not result[-1].text:
                result.append(item)
                continue
            previous = result[-1]
            if self._joinable(previous.text, item.text):
                if previous.text.endswith("-") and re.match(r"^[a-z]", item.text):
                    joined = previous.text[:-1] + item.text
                else:
                    spacer = "" if self._cjk_boundary(previous.text, item.text) else " "
                    joined = previous.text + spacer + item.text
                result[-1] = _MappedLine(joined, previous.start, item.end)
            else:
                result.append(item)
        return result

    def _joinable(self, left: str, right: str) -> bool:
        if self.MARKDOWN_HEADING.match(left) or self.MARKDOWN_HEADING.match(right):
            return False
        if self.CHAPTER_HEADING.match(right) or self.TOC_LINE.fullmatch(right):
            return False
        if re.match(r"^(?:[-*+]\s+|\d+[.)、]\s*)", right):
            return False
        if self._sentence_ended(left):
            return False
        return len(left) < 240

    @staticmethod
    def _sentence_ended(value: str) -> bool:
        return bool(re.search(r"[。！？.!?；;：:]$", value.strip()))

    @staticmethod
    def _cjk_boundary(left: str, right: str) -> bool:
        return bool(re.search(r"[\u3400-\u9fff]$", left) and re.match(r"^[\u3400-\u9fff]", right))

    def _recognize_headings(self, lines: list[_MappedLine]) -> list[_MappedLine]:
        output: list[_MappedLine] = []
        for item in lines:
            text = item.text.strip()
            if text and not self.MARKDOWN_HEADING.match(text) and self.CHAPTER_HEADING.match(text) and len(text) <= 100:
                text = "## " + text
            output.append(_MappedLine(text, item.start, item.end))
        return output

    @staticmethod
    def _collapse_blanks(lines: list[_MappedLine]) -> list[_MappedLine]:
        output: list[_MappedLine] = []
        for item in lines:
            if not item.text and (not output or not output[-1].text):
                continue
            output.append(item)
        while output and not output[-1].text:
            output.pop()
        return output
