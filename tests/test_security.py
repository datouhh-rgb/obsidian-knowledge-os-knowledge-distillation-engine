from __future__ import annotations

import json
import subprocess
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_engine.analyzer import BookAnalyzer
from knowledge_engine.evidence import EvidenceCatalog
from knowledge_engine.ingest import IngestError, SafeURLFetcher, SourceIngester
from knowledge_engine.providers import AnalyzerProviderError, LLMAnalyzerProvider
from knowledge_engine.utils import sha256_file


@pytest.mark.parametrize("url", [
    "http://127.0.0.1", "http://localhost", "http://10.0.0.1", "http://172.16.0.1",
    "http://192.168.1.1", "http://169.254.169.254/latest/meta-data", "http://[::1]", "http://[fe80::1]",
])
def test_private_and_metadata_urls_are_blocked(url: str) -> None:
    with pytest.raises(IngestError):
        SafeURLFetcher()._validate_url(url)


def test_public_redirect_to_private_is_blocked(monkeypatch) -> None:
    fetcher = SafeURLFetcher()

    def resolve(host: str) -> set[str]:
        return {"93.184.216.34"} if host == "public.test" else {"127.0.0.1"}

    monkeypatch.setattr(fetcher, "_resolve", resolve)

    class Opener:
        def open(self, request, timeout):  # type: ignore[no-untyped-def]
            raise urllib.error.HTTPError(request.full_url, 302, "redirect", {"Location": "http://127.0.0.1/private"}, None)

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    with pytest.raises(IngestError, match="非公网"):
        fetcher.fetch("http://public.test")


def _pdf_ingester(tmp_path: Path, monkeypatch, *, metadata_overrides=None, output_outside=False, timeout=False):
    source = tmp_path / "book.pdf"; source.write_bytes(b"%PDF-test")
    wrapper = tmp_path / "wrapper.ps1"; wrapper.write_text("# mock", encoding="utf-8")
    work = tmp_path / "work"; work.mkdir()

    def run(command, **kwargs):  # type: ignore[no-untyped-def]
        if timeout:
            raise subprocess.TimeoutExpired(command, 1)
        output = (tmp_path / "outside.md") if output_outside else (work / "pdf/markitdown-output.md")
        output.parent.mkdir(parents=True, exist_ok=True); output.write_text("# Converted\n\ntext\n", encoding="utf-8")
        metadata_path = Path(str(output) + ".meta.json")
        metadata = {
            "status": "ok", "source_sha256": sha256_file(source), "converter": "Microsoft MarkItDown",
            "markitdown_version": "1.0", "warnings": [],
        }
        metadata.update(metadata_overrides or {})
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        payload = {"status": "ok", "markdown_path": str(output), "metadata_path": str(metadata_path), "warnings": []}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", run)
    return SourceIngester(work, pdf_wrapper=wrapper, timeout=1), source


def test_pdf_timeout_is_blocked(tmp_path: Path, monkeypatch) -> None:
    ingester, source = _pdf_ingester(tmp_path, monkeypatch, timeout=True)
    with pytest.raises(IngestError, match="超时"):
        ingester.ingest(str(source))


def test_pdf_outside_workdir_is_blocked(tmp_path: Path, monkeypatch) -> None:
    ingester, source = _pdf_ingester(tmp_path, monkeypatch, output_outside=True)
    with pytest.raises(IngestError, match="work_dir"):
        ingester.ingest(str(source))


def test_pdf_forged_metadata_and_wrong_hash_are_blocked(tmp_path: Path, monkeypatch) -> None:
    ingester, source = _pdf_ingester(tmp_path, monkeypatch, metadata_overrides={"source_sha256": "0" * 64})
    with pytest.raises(IngestError, match="source hash"):
        ingester.ingest(str(source))


def test_pdf_oversize_output_is_blocked(tmp_path: Path, monkeypatch) -> None:
    ingester, source = _pdf_ingester(tmp_path, monkeypatch)
    ingester.MAX_PDF_OUTPUT_BYTES = 4
    with pytest.raises(IngestError, match="超过"):
        ingester.ingest(str(source))


class FakeTransport:
    name = "fake"
    model = "fake-model"
    config = {}

    def __init__(self, payload):
        self.payload = payload

    def complete(self, request):  # type: ignore[no-untyped-def]
        return self.payload


def _valid_llm_payload(evidence_index: int) -> dict:
    statement = lambda text: {"text": text, "selected_evidence_indexes": [evidence_index]}
    return {
        "concepts": [], "relations": [],
        "skills": [{
            "title": "核验流程", "positioning": statement("保存原始资料并计算 SHA-256。"),
            "scenarios": [statement("保存原始资料并计算 SHA-256。")],
            "input_requirements": [statement("保存原始资料并计算 SHA-256。")],
            "decision_flow": [statement("保存原始资料并计算 SHA-256。"), statement("记录原文行号和摘录。")],
            "actions": [statement("保存原始资料并计算 SHA-256。"), statement("记录原文行号和摘录。")],
            "output_format": statement("记录原文行号和摘录。"),
            "boundaries": [statement("记录原文行号和摘录。")],
            "prohibited_scenarios": [statement("记录原文行号和摘录。")], "confidence": 0.8,
        }],
    }


def _analysis_catalog():
    text = "# T\n\n## 流程\n\n1. 保存原始资料并计算 SHA-256。\n2. 记录原文行号和摘录。\n"
    analysis = BookAnalyzer().analyze(text, "T")
    catalog = EvidenceCatalog.from_text("T", "a" * 64, text, "b" * 64)
    return analysis, catalog


@pytest.mark.parametrize("mutation", ["too-many", "too-long", "unknown", "path-injection", "command-injection"])
def test_malicious_llm_payloads_are_blocked(mutation: str) -> None:
    analysis, catalog = _analysis_catalog()
    payload = _valid_llm_payload(1)
    if mutation == "too-many": payload["skills"] *= 101
    elif mutation == "too-long": payload["skills"][0]["title"] = "x" * 10001
    elif mutation == "unknown": payload["unexpected"] = True
    elif mutation == "path-injection": payload["skills"][0]["title"] = "C:\\Windows\\secret"
    else: payload["skills"][0]["title"] = "powershell run"
    with pytest.raises(AnalyzerProviderError):
        LLMAnalyzerProvider(FakeTransport(payload)).propose(analysis, catalog)
