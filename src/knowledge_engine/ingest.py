from __future__ import annotations

import ipaddress
import json
import mimetypes
import os
import re
import shlex
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from jsonschema import Draft202012Validator

from .conversion import (
    CANONICAL_CONVERSION_SCHEMA,
    CanonicalConversionError,
    convert_epub_archive,
    convert_epub_directory,
)
from .models import IngestedSource
from .utils import canonical_json, normalize_text, safe_filename, safe_relative_target, sha256_file, sha256_text


class IngestError(RuntimeError):
    pass


class _MarkdownHTMLParser(HTMLParser):
    HEADING_LEVELS = {f"h{i}": i for i in range(1, 7)}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0
        self.image_omissions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
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
        tag = tag.lower()
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


class _NoAutomaticRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class SafeURLFetcher:
    ALLOWED_TYPES = {"text/html", "application/xhtml+xml", "text/plain", "text/markdown"}
    BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "metadata.google.internal"}

    def __init__(
        self,
        timeout: int = 30,
        max_bytes: int = 25 * 1024 * 1024,
        max_redirects: int = 5,
        allowlist: tuple[str, ...] = (),
    ) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.allowlist = tuple(item.casefold().rstrip(".") for item in allowlist)
        self.audit: list[dict[str, Any]] = []

    def fetch(self, url: str) -> tuple[bytes, str, str, list[dict[str, Any]]]:
        current = url
        opener = urllib.request.build_opener(_NoAutomaticRedirect())
        for redirect_count in range(self.max_redirects + 1):
            host, addresses = self._validate_url(current)
            self.audit.append({"action": "url-allow", "url": current, "host": host, "addresses": sorted(addresses), "allowlist": bool(self.allowlist)})
            request = urllib.request.Request(current, headers={"User-Agent": "KnowledgeDistillationEngine/3.0"})
            try:
                response = opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    if not location:
                        raise IngestError("URL redirect 缺少 Location。") from exc
                    if redirect_count >= self.max_redirects:
                        raise IngestError("URL redirect 超过安全上限。") from exc
                    current = urllib.parse.urljoin(current, location)
                    continue
                raise IngestError(f"网页抓取失败：HTTP {exc.code}") from exc
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                raise IngestError(f"网页抓取失败：{type(exc).__name__}: {exc}") from exc
            with response:
                final_url = response.geturl()
                if final_url != current:
                    self._validate_url(final_url)
                post_addresses = self._resolve(host)
                if post_addresses != addresses:
                    raise IngestError("检测到 DNS rebinding：连接前后解析结果发生变化。")
                content_type = response.headers.get_content_type().casefold()
                if content_type not in self.ALLOWED_TYPES:
                    raise IngestError(f"网页返回不支持的 Content-Type：{content_type}")
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > self.max_bytes:
                    raise IngestError(f"网页响应超过安全上限 {self.max_bytes} bytes。")
                raw = response.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    raise IngestError(f"网页响应超过安全上限 {self.max_bytes} bytes。")
                charset = response.headers.get_content_charset() or "utf-8"
                return raw, content_type, charset, list(self.audit)
        raise IngestError("URL redirect 状态异常。")

    def _validate_url(self, url: str) -> tuple[str, set[str]]:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.casefold() not in {"http", "https"}:
            raise IngestError("URL scheme 只允许公开 HTTP(S)。")
        if parsed.username or parsed.password:
            raise IngestError("URL 不允许包含凭据。")
        host = (parsed.hostname or "").casefold().rstrip(".")
        if not host or host in self.BLOCKED_HOSTS:
            raise IngestError(f"URL host 被阻断：{host or '(empty)'}")
        try:
            port = parsed.port
        except ValueError as exc:
            raise IngestError("URL port 无效。") from exc
        effective_port = port or (443 if parsed.scheme.casefold() == "https" else 80)
        if effective_port not in {80, 443}:
            raise IngestError(f"URL port 不在允许列表：{effective_port}")
        if self.allowlist and not any(host == item or host.endswith("." + item) for item in self.allowlist):
            raise IngestError(f"URL host 不在显式 allowlist：{host}")
        addresses = self._resolve(host)
        for raw in addresses:
            address = ipaddress.ip_address(raw)
            if not address.is_global or address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified or address.is_multicast:
                raise IngestError(f"URL 解析到非公网地址，已阻断：{address}")
        return host, addresses

    @staticmethod
    def _resolve(host: str) -> set[str]:
        try:
            return {item[4][0].split("%", 1)[0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
        except socket.gaierror as exc:
            raise IngestError(f"URL DNS 解析失败：{host}: {exc}") from exc


class SourceIngester:
    SUPPORTED_EXTENSIONS = {".pdf", ".epub", ".azw3", ".md", ".markdown", ".txt", ".ocr", ".docx", ".html", ".htm"}
    MAX_LOCAL_BYTES = 512 * 1024 * 1024
    MAX_WEB_BYTES = 25 * 1024 * 1024
    MAX_DOCX_XML_BYTES = 64 * 1024 * 1024
    MAX_PDF_OUTPUT_BYTES = 256 * 1024 * 1024

    def __init__(
        self,
        work_dir: Path,
        pdf_wrapper: Path | None = None,
        timeout: int = 60,
        url_allowlist: tuple[str, ...] = (),
        azw3_command: tuple[str, ...] | None = None,
    ) -> None:
        self.work_dir = work_dir.resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.url_allowlist = url_allowlist
        configured = os.environ.get("BOOK_DISTILLER_PDF_WRAPPER")
        self.pdf_wrapper = pdf_wrapper or (Path(configured) if configured else self._default_pdf_wrapper())
        configured_azw3 = os.environ.get("BOOK_DISTILLER_AZW3_COMMAND", "")
        self.azw3_command = azw3_command or (tuple(shlex.split(configured_azw3, posix=os.name != "nt")) if configured_azw3 else None)

    def ingest(self, source: str) -> IngestedSource:
        if re.match(r"^https?://", source, flags=re.IGNORECASE):
            return self._ingest_url(source)
        path = Path(os.path.expandvars(os.path.expanduser(source))).resolve()
        if not path.is_file():
            raise IngestError(f"输入文件不存在：{path}")
        if path.stat().st_size > self.MAX_LOCAL_BYTES:
            raise IngestError(f"输入文件超过 V3 安全上限 {self.MAX_LOCAL_BYTES} bytes：{path}")
        if path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            raise IngestError(f"不支持的格式 {path.suffix or '(无扩展名)'}。")
        return self._ingest_local(path, source)

    def _ingest_url(self, url: str) -> IngestedSource:
        fetcher = SafeURLFetcher(timeout=self.timeout, max_bytes=self.MAX_WEB_BYTES, allowlist=self.url_allowlist)
        raw, content_type, charset, audit = fetcher.fetch(url)
        parsed = urllib.parse.urlsplit(url)
        fallback = safe_filename(parsed.netloc + (parsed.path or "-index"), "webpage") + ".html"
        raw_path = safe_relative_target(self.work_dir, fallback)
        raw_path.write_bytes(raw)
        warnings: list[str] = []
        try:
            decoded = raw.decode(charset, errors="strict")
        except (LookupError, UnicodeDecodeError) as exc:
            raise IngestError(f"网页声明编码 {charset!r} 无法可靠解码；已 fail closed：{exc}") from exc
        text = self._html_to_markdown(decoded) if "html" in content_type else normalize_text(decoded)
        normalized_output = normalize_text(text)
        return IngestedSource(
            input_ref=url, local_path=raw_path, original_name=fallback, media_type=content_type,
            source_kind="webpage", sha256=sha256_file(raw_path), text=normalized_output,
            converter_metadata={"converter": "stdlib HTMLParser", "version": "3", "config": {
                "content_type": content_type,
                "normalized_output_sha256": sha256_text(normalized_output),
            }, "url_audit": audit},
            warnings=warnings,
        )

    def _ingest_local(self, path: Path, input_ref: str) -> IngestedSource:
        suffix = path.suffix.lower()
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        warnings: list[str] = []
        metadata: dict[str, Any]
        if suffix == ".pdf":
            text, metadata, warnings = self._convert_pdf(path)
            kind = "pdf"
        elif suffix == ".epub":
            text, metadata, warnings = self._epub_to_markdown(path)
            kind = "epub"
        elif suffix == ".azw3":
            text, metadata, warnings = self._convert_azw3(path)
            kind = "azw3"
        elif suffix == ".docx":
            text = self._docx_to_markdown(path)
            metadata = {"converter": "stdlib DOCX XML", "version": "3", "config": {}}
            kind = "word"
        elif suffix in {".html", ".htm"}:
            raw, warning = self._read_text(path)
            text = self._html_to_markdown(raw)
            metadata = {"converter": "stdlib HTMLParser", "version": "3", "config": {}}
            kind = "html"
            if warning:
                warnings.append(warning)
        else:
            text, warning = self._read_text(path)
            metadata = {"converter": "identity-text", "version": "3", "config": {"encoding": "utf-8"}}
            kind = "ocr" if suffix == ".ocr" else "markdown" if suffix in {".md", ".markdown"} else "text"
            if warning:
                warnings.append(warning)
        if not text.strip():
            raise IngestError("转换结果为空。")
        normalized_output = normalize_text(text)
        config = metadata.get("config", {})
        if not isinstance(config, dict):
            config = {"legacy_config": config}
        config.setdefault("normalized_output_sha256", sha256_text(normalized_output))
        metadata["config"] = config
        metadata.setdefault("output_sha256", sha256_text(normalized_output))
        return IngestedSource(input_ref, path, path.name, media_type, kind, sha256_file(path), normalized_output, metadata, warnings)

    def _epub_to_markdown(self, path: Path) -> tuple[str, dict[str, object], list[str]]:
        try:
            with zipfile.ZipFile(path) as archive:
                self._validate_ebook_archive(archive)
            converted = convert_epub_archive(path)
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, CanonicalConversionError) as exc:
            raise IngestError(f"EPUB 结构或字符解码无效：{exc}") from exc
        metadata: dict[str, object] = {
            "converter": "canonical EPUB spine adapter",
            "version": CANONICAL_CONVERSION_SCHEMA,
            "config": {
                **converted.metadata,
                "image_policy": "declare-omission",
                "cache_policy": "disabled-canonical-acceptance",
            },
            "source_id": sha256_file(path),
            "output_sha256": sha256_text(converted.text),
        }
        return converted.text, metadata, list(converted.warnings)

    def _convert_azw3(self, path: Path) -> tuple[str, dict[str, object], list[str]]:
        if not self.azw3_command:
            raise IngestError(
                "AZW3 requires an explicit local converter command in BOOK_DISTILLER_AZW3_COMMAND; "
                "use {input} and {output_dir} placeholders."
            )
        template_text = "\0".join(self.azw3_command)
        if "{input}" not in template_text or "{output_dir}" not in template_text:
            raise IngestError("Canonical AZW3 command must explicitly contain {input} and {output_dir}.")
        if "{output}" in template_text:
            raise IngestError("Legacy single-file {output} AZW3 conversion is prohibited; use canonical {output_dir} unpacking.")
        output_dir = safe_relative_target(self.work_dir, Path("azw3") / "kindleunpack")
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [item.format(input=str(path), output_dir=str(output_dir)) for item in self.azw3_command]
        source_hash_before = sha256_file(path)
        command_contract = [
            "{input}" if item == str(path) else "{output_dir}" if item == str(output_dir) else Path(item).name if Path(item).is_absolute() else item
            for item in command
        ]
        command_fingerprint = sha256_text(canonical_json({
            "schema": CANONICAL_CONVERSION_SCHEMA,
            "argv": command_contract,
            "shell": False,
            "encoding": "utf-8-strict",
        }))
        environment = dict(os.environ)
        environment["PYTHONUTF8"] = "1"
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", errors="strict",
                timeout=self.timeout, shell=False, env=environment,
            )
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            raise IngestError(f"AZW3 local converter failed: {type(exc).__name__}: {exc}") from exc
        if completed.returncode != 0:
            raise IngestError(f"AZW3 local converter returned {completed.returncode}: {completed.stderr.strip()}")
        if sha256_file(path) != source_hash_before:
            raise IngestError("AZW3 converter modified the immutable original source.")
        try:
            converted = convert_epub_directory(output_dir)
        except CanonicalConversionError as exc:
            raise IngestError(f"KindleUnpack output failed canonical EPUB parsing: {exc}") from exc
        version_match = re.search(r"KindleUnpack\s+v([^\s]+)", completed.stdout)
        converter_version = version_match.group(1) if version_match else "unreported"
        converter_commit = self._converter_commit(command)
        metadata: dict[str, object] = {
            "converter": "KindleUnpack canonical EPUB spine adapter",
            "version": f"KindleUnpack-{converter_version}+{CANONICAL_CONVERSION_SCHEMA}",
            "config": {
                **converted.metadata,
                "converter_name": "KindleUnpack",
                "converter_version": converter_version,
                "converter_commit": converter_commit,
                "command_fingerprint": command_fingerprint,
                "command_config": command_contract,
                "conversion_schema_version": CANONICAL_CONVERSION_SCHEMA,
                "source_bytes_sha256": source_hash_before,
                "converter_input_attestation": {
                    "input_placeholder_present": True,
                    "exact_original_path_supplied": True,
                    "source_sha256_before": source_hash_before,
                    "source_sha256_after": sha256_file(path),
                },
                "original_source_name": path.name,
                "original_source_path_redacted": True,
                "cache_policy": "disabled-canonical-acceptance",
            },
            "source_id": source_hash_before,
            "output_sha256": sha256_text(converted.text),
        }
        warnings = [line for line in completed.stderr.splitlines() if line.strip()][:100]
        warnings.extend(item for item in converted.warnings if item not in warnings)
        return converted.text, metadata, warnings

    @staticmethod
    def _converter_commit(command: list[str]) -> str:
        candidates = [Path(item) for item in command if item.casefold().endswith((".py", ".pyw"))]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            completed = subprocess.run(
                ["git", "-C", str(candidate.parent), "rev-parse", "HEAD"],
                capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=10, check=False,
            )
            commit = completed.stdout.strip().casefold()
            if completed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", commit):
                return commit
            return "script-sha256:" + sha256_file(candidate)
        return "unresolved"

    @staticmethod
    def _validate_ebook_archive(archive: zipfile.ZipFile) -> None:
        total = 0
        for info in archive.infolist():
            member = Path(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise IngestError(f"EPUB contains unsafe path: {info.filename}")
            total += info.file_size
            if total > SourceIngester.MAX_LOCAL_BYTES * 2:
                raise IngestError("EPUB expanded content exceeds the safety budget.")
            if info.compress_size and info.file_size / info.compress_size > 500:
                raise IngestError(f"EPUB member has suspicious compression ratio: {info.filename}")

    def _convert_pdf(self, path: Path) -> tuple[str, dict[str, object], list[str]]:
        if not self.pdf_wrapper.is_file():
            raise IngestError(f"未找到强制 PDF 转换器 Microsoft MarkItDown wrapper：{self.pdf_wrapper}")
        markdown_path = safe_relative_target(self.work_dir, Path("pdf") / "markitdown-output.md")
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.pdf_wrapper),
            "-InputPath", str(path), "-OutputPath", str(markdown_path), "-Json", "-NoCache",
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout, shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise IngestError(f"Microsoft MarkItDown 转换超时（{self.timeout}s）。") from exc
        payload = self._parse_json_output(completed.stdout)
        if completed.returncode != 0 or payload.get("status") == "failed":
            raise IngestError(f"Microsoft MarkItDown 转换失败：{payload.get('error') or completed.stderr.strip() or 'unknown'}")
        if str(payload.get("status")) not in {"ok", "warning"}:
            raise IngestError("Microsoft MarkItDown 返回未知状态。")
        output = self._confined_reported_path(payload.get("markdown_path"), markdown_path)
        metadata_path = self._confined_reported_path(payload.get("metadata_path"), Path(str(markdown_path) + ".meta.json"))
        if not output.is_file() or not metadata_path.is_file():
            raise IngestError("MarkItDown 未生成 Markdown 或元数据文件。")
        if output.stat().st_size > self.MAX_PDF_OUTPUT_BYTES or metadata_path.stat().st_size > 1_000_000:
            raise IngestError("MarkItDown 输出超过安全上限。")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise IngestError(f"MarkItDown metadata 无效：{exc}") from exc
        errors = sorted(Draft202012Validator(self._pdf_metadata_schema()).iter_errors(metadata), key=lambda item: list(item.path))
        if errors:
            raise IngestError(f"MarkItDown metadata schema 失败：{errors[0].json_path}: {errors[0].message}")
        source_id = sha256_file(path)
        if metadata["source_sha256"] != source_id:
            raise IngestError("MarkItDown metadata source hash 与原件不一致。")
        if metadata["converter"] != "Microsoft MarkItDown":
            raise IngestError("PDF converter 不是 Microsoft MarkItDown。")
        output_hash = sha256_file(output)
        verified = dict(metadata)
        verified.update({
            "version": str(metadata["markitdown_version"]),
            "config": {"enable_plugins": False, "no_cache": True},
            "source_id": source_id,
            "output_sha256": output_hash,
            "wrapper_validation": "v3-strict",
        })
        warnings = [str(item) for item in payload.get("warnings", [])]
        warnings.extend(str(item) for item in metadata.get("warnings", []) if str(item) not in warnings)
        return output.read_text(encoding="utf-8"), verified, warnings

    def _confined_reported_path(self, value: object, fallback: Path) -> Path:
        reported = Path(str(value or fallback))
        target = reported.resolve(strict=False) if reported.is_absolute() else safe_relative_target(self.work_dir, reported)
        try:
            target.relative_to(self.work_dir)
        except ValueError as exc:
            raise IngestError(f"PDF wrapper 报告了 work_dir 之外的路径：{reported}") from exc
        return target

    @staticmethod
    def _pdf_metadata_schema() -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["status", "source_sha256", "converter", "markitdown_version", "warnings"],
            "properties": {
                "status": {"enum": ["ok", "warning"]},
                "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "converter": {"const": "Microsoft MarkItDown"},
                "markitdown_version": {"type": "string", "minLength": 1, "maxLength": 100},
                "warnings": {"type": "array", "maxItems": 100, "items": {"type": "string", "maxLength": 10000}},
            },
            "additionalProperties": True,
        }

    @staticmethod
    def _parse_json_output(stdout: str) -> dict[str, object]:
        if len(stdout) > 1_000_000:
            raise IngestError("MarkItDown stdout 超过安全上限。")
        for line in reversed(stdout.splitlines()):
            try:
                payload = json.loads(line.strip().lstrip("\ufeff"))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise IngestError("无法解析 MarkItDown 的 JSON 输出。")

    @staticmethod
    def _read_text(path: Path) -> tuple[str, str | None]:
        try:
            return path.read_text(encoding="utf-8-sig"), None
        except UnicodeDecodeError as exc:
            raise IngestError(f"源文本不是可靠 UTF-8；禁止 replacement 解码：{path.name}: {exc}") from exc

    @staticmethod
    def _html_to_markdown(html: str) -> str:
        parser = _MarkdownHTMLParser()
        parser.feed(html)
        return parser.markdown()

    @staticmethod
    def _docx_to_markdown(path: Path) -> str:
        try:
            with zipfile.ZipFile(path) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > SourceIngester.MAX_DOCX_XML_BYTES:
                    raise IngestError("DOCX document.xml 解压后超过安全上限。")
                if info.compress_size and info.file_size / info.compress_size > 200:
                    raise IngestError("DOCX document.xml 压缩比异常，疑似 ZIP bomb。")
                xml = archive.read(info)
        except (zipfile.BadZipFile, KeyError) as exc:
            raise IngestError(f"DOCX 结构无效：{exc}") from exc
        root = ElementTree.fromstring(xml)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        output: list[str] = []
        for paragraph in root.findall(".//w:body/w:p", ns):
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns)).strip()
            if not text:
                continue
            style = paragraph.find("./w:pPr/w:pStyle", ns)
            style_value = "" if style is None else style.attrib.get(f"{{{ns['w']}}}val", "")
            heading_match = re.match(r"Heading\s*([1-6])", style_value, flags=re.IGNORECASE)
            if heading_match:
                output.append("#" * int(heading_match.group(1)) + " " + text)
            elif paragraph.find("./w:pPr/w:numPr", ns) is not None:
                output.append("- " + text)
            else:
                output.append(text)
        if not output:
            raise IngestError("DOCX 中没有可提取的正文。")
        return "\n\n".join(output) + "\n"

    @staticmethod
    def _default_pdf_wrapper() -> Path:
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        return codex_home / "tools" / "markitdown" / "pdf-to-md.ps1"
