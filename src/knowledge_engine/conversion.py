from __future__ import annotations

import codecs
import re
import urllib.parse
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Mapping
from xml.etree import ElementTree

from .utils import canonical_json, normalize_text, sha256_bytes, sha256_text


CANONICAL_CONVERSION_SCHEMA = "canonical-source-conversion-v1"
CONTROLLED_FALLBACKS = ("gb18030", "big5")


class CanonicalConversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class DecodedMarkup:
    text: str
    encoding: str
    strategy: str
    decoded_sha256: str
    attempts: tuple[str, ...]


@dataclass(frozen=True)
class CanonicalEbookDocument:
    text: str
    metadata: dict[str, object]
    warnings: tuple[str, ...]


class _MarkupToMarkdown(HTMLParser):
    HEADING_LEVELS = {f"h{i}": i for i in range(1, 7)}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0
        self.image_omissions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored_depth += 1
        elif not self.ignored_depth and tag in self.HEADING_LEVELS:
            self.parts.append("\n" + "#" * self.HEADING_LEVELS[tag] + " ")
        elif not self.ignored_depth and tag == "li":
            self.parts.append("\n- ")
        elif not self.ignored_depth and tag in {"p", "div", "section", "article", "br"}:
            self.parts.append("\n")
        elif not self.ignored_depth and tag == "img":
            attributes = dict(attrs)
            reference = str(attributes.get("src") or attributes.get("alt") or "unidentified image")
            self.image_omissions.append(reference)
            self.parts.append(f"\n> [IMAGE OMITTED: {reference}]\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored_depth = max(0, self.ignored_depth - 1)
        elif not self.ignored_depth and (tag in self.HEADING_LEVELS or tag in {"li", "p", "div", "section", "article"}):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth and data.strip():
            self.parts.append(re.sub(r"\s+", " ", data))

    def markdown(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return normalize_text(text)


def _declared_encoding(raw: bytes) -> tuple[str | None, str | None]:
    prefix = raw[:4096].decode("latin-1")
    xml = re.search(r"<\?xml[^>]+encoding\s*=\s*['\"]\s*([^'\"\s]+)", prefix, re.IGNORECASE)
    if xml:
        return xml.group(1), "xml-declaration"
    meta = re.search(r"<meta[^>]+charset\s*=\s*['\"]?\s*([^\s'\"/>;]+)", prefix, re.IGNORECASE)
    if meta:
        return meta.group(1), "html-meta-charset"
    content_type = re.search(
        r"<meta[^>]+content\s*=\s*['\"][^'\"]*charset\s*=\s*([^\s;'\"]+)",
        prefix,
        re.IGNORECASE,
    )
    if content_type:
        return content_type.group(1), "html-meta-content-type"
    return None, None


def _strict_decode(raw: bytes, encoding: str, strategy: str, attempts: list[str]) -> DecodedMarkup | None:
    try:
        canonical = codecs.lookup(encoding).name
    except LookupError:
        attempts.append(f"{strategy}:{encoding}:unknown-codec")
        return None
    try:
        text = raw.decode(canonical, errors="strict")
    except UnicodeDecodeError as exc:
        attempts.append(f"{strategy}:{canonical}:decode-error@{exc.start}")
        return None
    attempts.append(f"{strategy}:{canonical}:passed")
    return DecodedMarkup(text, canonical, strategy, sha256_text(text), tuple(attempts))


def _controlled_fallback_valid(raw: bytes, decoded: str, encoding: str) -> bool:
    try:
        if decoded.encode(encoding, errors="strict") != raw:
            return False
    except UnicodeError:
        return False
    controls = sum(ord(char) < 32 and char not in "\t\r\n" for char in decoded)
    cjk = len(re.findall(r"[\u3400-\u9fff]", decoded))
    semantic = len(re.findall(r"[A-Za-z\u3400-\u9fff]", decoded))
    return controls == 0 and semantic >= 4 and cjk >= max(2, semantic // 5)


def decode_markup_member(raw: bytes, member: str) -> DecodedMarkup:
    attempts: list[str] = []
    boms = (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF32_LE, "utf-32-le"),
        (codecs.BOM_UTF32_BE, "utf-32-be"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    )
    for bom, encoding in boms:
        if raw.startswith(bom):
            decoded = _strict_decode(raw, encoding, "bom", attempts)
            if decoded is not None:
                return decoded
            break
    declared, strategy = _declared_encoding(raw)
    if declared:
        decoded = _strict_decode(raw, declared, str(strategy), attempts)
        if decoded is not None:
            return decoded
    decoded = _strict_decode(raw, "utf-8", "strict-utf8", attempts)
    if decoded is not None:
        return decoded
    viable: list[tuple[int, str, str]] = []
    for encoding in CONTROLLED_FALLBACKS:
        try:
            text = raw.decode(encoding, errors="strict")
        except UnicodeDecodeError as exc:
            attempts.append(f"controlled-fallback:{encoding}:decode-error@{exc.start}")
            continue
        if not _controlled_fallback_valid(raw, text, encoding):
            attempts.append(f"controlled-fallback:{encoding}:audit-rejected")
            continue
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        viable.append((cjk, encoding, text))
        attempts.append(f"controlled-fallback:{encoding}:audited")
    if not viable:
        raise CanonicalConversionError(
            f"Cannot reliably decode spine member {member}; attempts=" + ", ".join(attempts)
        )
    _score, encoding, text = sorted(viable, key=lambda item: (-item[0], CONTROLLED_FALLBACKS.index(item[1])))[0]
    return DecodedMarkup(
        text,
        encoding,
        "controlled-fallback-audited",
        sha256_text(text),
        tuple(attempts),
    )


def _safe_member(value: str) -> str:
    normalized = urllib.parse.unquote(value.split("#", 1)[0]).replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise CanonicalConversionError(f"Unsafe EPUB member path: {value}")
    return path.as_posix()


def _join_member(base: str, value: str) -> str:
    joined = PurePosixPath(base).parent / PurePosixPath(_safe_member(value))
    return _safe_member(joined.as_posix())


def _parse_epub_members(
    members: Mapping[str, bytes],
    *,
    container_label: str,
    excluded_derived_members: tuple[str, ...] = (),
    container_manifest_policy: str = "all-archive-members-v1",
) -> CanonicalEbookDocument:
    canonical_members = {_safe_member(name): raw for name, raw in members.items()}
    containers = sorted(name for name in canonical_members if name.endswith("META-INF/container.xml"))
    if len(containers) != 1:
        raise CanonicalConversionError(f"Expected exactly one EPUB container.xml, found {len(containers)}")
    container_path = containers[0]
    container_prefix = container_path[: -len("META-INF/container.xml")]
    try:
        container = ElementTree.fromstring(canonical_members[container_path])
    except ElementTree.ParseError as exc:
        raise CanonicalConversionError(f"Invalid EPUB container.xml: {exc}") from exc
    rootfile_relative = next(
        (item.attrib.get("full-path", "") for item in container.iter() if item.tag.rsplit("}", 1)[-1] == "rootfile"),
        "",
    )
    if not rootfile_relative:
        raise CanonicalConversionError("EPUB container.xml has no rootfile")
    rootfile = _safe_member(container_prefix + _safe_member(rootfile_relative))
    if rootfile not in canonical_members:
        raise CanonicalConversionError(f"EPUB rootfile is missing: {rootfile}")
    try:
        opf = ElementTree.fromstring(canonical_members[rootfile])
    except ElementTree.ParseError as exc:
        raise CanonicalConversionError(f"Invalid EPUB OPF {rootfile}: {exc}") from exc
    manifest: dict[str, tuple[str, str]] = {}
    for item in opf.iter():
        if item.tag.rsplit("}", 1)[-1] != "item":
            continue
        identifier = item.attrib.get("id", "")
        href = item.attrib.get("href", "")
        if identifier and href:
            manifest[identifier] = (href, item.attrib.get("media-type", ""))
    spine_ids = [item.attrib.get("idref", "") for item in opf.iter() if item.tag.rsplit("}", 1)[-1] == "itemref"]
    if not spine_ids:
        raise CanonicalConversionError("EPUB OPF has no spine")
    chapters: list[str] = []
    spine_records: list[dict[str, object]] = []
    omissions: list[str] = []
    empty_members: list[str] = []
    for order, identifier in enumerate(spine_ids, start=1):
        declared = manifest.get(identifier)
        if declared is None:
            raise CanonicalConversionError(f"EPUB spine id is absent from manifest: {identifier}")
        href, media_type = declared
        member = _join_member(rootfile, href)
        if member not in canonical_members:
            raise CanonicalConversionError(f"EPUB spine member is missing: {member}")
        raw = canonical_members[member]
        decoded = decode_markup_member(raw, member)
        parser = _MarkupToMarkdown()
        parser.feed(decoded.text)
        chapter = parser.markdown()
        if chapter.strip():
            chapters.append(chapter)
        else:
            empty_members.append(member)
        omissions.extend(f"{member}:{item}" for item in parser.image_omissions)
        spine_records.append({
            "order": order,
            "idref": identifier,
            "path": member,
            "media_type": media_type,
            "raw_sha256": sha256_bytes(raw),
            "raw_bytes": len(raw),
            "detected_encoding": decoded.encoding,
            "decoding_strategy": decoded.strategy,
            "decoding_attempts": list(decoded.attempts),
            "decoded_sha256": decoded.decoded_sha256,
            "markdown_sha256": sha256_text(chapter),
            "markdown_characters": len(chapter),
            "image_omissions": list(parser.image_omissions),
        })
    if not chapters:
        raise CanonicalConversionError("EPUB spine contains no extractable body text")
    normalized = normalize_text("\n\n".join(chapters))
    manifest_rows = [
        {"path": name, "sha256": sha256_bytes(raw), "bytes": len(raw)}
        for name, raw in sorted(canonical_members.items())
    ]
    metadata: dict[str, object] = {
        "conversion_schema_version": CANONICAL_CONVERSION_SCHEMA,
        "container_label": container_label,
        "container_path": container_path,
        "unpacked_container_hash": sha256_text(canonical_json(manifest_rows)),
        "unpacked_member_count": len(manifest_rows),
        "container_manifest_policy": container_manifest_policy,
        "excluded_derived_members": list(excluded_derived_members),
        "opf_rootfile": rootfile,
        "spine_order": [str(item["path"]) for item in spine_records],
        "spine_members": spine_records,
        "empty_spine_members": empty_members,
        "missing_resources": [],
        "image_omissions": omissions,
        "normalized_output_sha256": sha256_text(normalized),
        "normalized_output_characters": len(normalized),
    }
    warnings = tuple(
        item for item in (
            f"EPUB contains {len(omissions)} declared image omission(s)." if omissions else "",
            f"EPUB contains {len(empty_members)} empty spine member(s)." if empty_members else "",
        ) if item
    )
    return CanonicalEbookDocument(normalized, metadata, warnings)


def convert_epub_archive(path: Path) -> CanonicalEbookDocument:
    try:
        with zipfile.ZipFile(path) as archive:
            members = {item.filename: archive.read(item) for item in archive.infolist() if not item.is_dir()}
    except (OSError, zipfile.BadZipFile) as exc:
        raise CanonicalConversionError(f"Invalid EPUB archive: {exc}") from exc
    return _parse_epub_members(members, container_label=path.name)


def convert_epub_directory(path: Path) -> CanonicalEbookDocument:
    root = path.resolve()
    members: dict[str, bytes] = {}
    excluded_derived_members: list[str] = []
    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(root).as_posix()
        # KindleUnpack emits a convenience EPUB beside the extracted canonical
        # members.  Its ZIP timestamps are nondeterministic and it is not an
        # OPF/spine input, so hashing it would make identical conversions drift.
        if item.suffix.casefold() == ".epub":
            excluded_derived_members.append(relative)
            continue
        members[relative] = item.read_bytes()
    return _parse_epub_members(
        members,
        container_label=path.name,
        excluded_derived_members=tuple(excluded_derived_members),
        container_manifest_policy="canonical-members-exclude-derived-archives-v1",
    )
