from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.real_codex


def test_codex_skill_exposure(real_codex_audit) -> None:  # type: ignore[no-untyped-def]
    exposure = real_codex_audit["exposure"]
    assert exposure["deployment_mode"] in {"symlink", "managed-cache"}
    assert exposure["authoritative_source"] == "vault-approved-immutable-version"
    assert exposure["create_status"] == "exposed"
    assert exposure["health_status"] == "healthy"
    assert real_codex_audit["checks"]["codex_discovery"] is True
    assert exposure["unexpose_status"] == "unexposed"
    assert exposure["removed_after_test"] is True
    assert real_codex_audit["vault"]["production_vault_opened"] is False


def test_codex_skill_metadata(real_codex_audit) -> None:  # type: ignore[no-untyped-def]
    skill = real_codex_audit["skill"]
    frontmatter = skill["frontmatter"]
    assert frontmatter["name"] == skill["name"]
    description = frontmatter["description"]
    assert "Apply " in description
    assert "Use when" in description
    assert "Do not use" in description
    assert set(frontmatter) == {"name", "description"}
    assert "description" in skill["trigger_contract"]
    assert skill["openai_yaml_policy"] == "allow_implicit_invocation: false"
    assert real_codex_audit["official_skill_contract"] == "https://developers.openai.com/codex/skills/"


def test_codex_explicit_skill_call(real_codex_audit) -> None:  # type: ignore[no-untyped-def]
    session = real_codex_audit["sessions"]["explicit"]
    assert session["exit_code"] == 0
    assert session["observed_skill_read"] is True
    assert session["detection"] == "raw-codex-event-read-target-skill"
    assert real_codex_audit["skill"]["invocation_fingerprint"] in session["last_message"]
    assert real_codex_audit["review"]["automatic"] is False
    assert real_codex_audit["review"]["before_status"] == "candidate"
    assert real_codex_audit["review"]["after_status"] == "approved"


def test_real_codex_reads_exact_skill_md(real_codex_audit) -> None:  # type: ignore[no-untyped-def]
    session = real_codex_audit["sessions"]["explicit"]
    raw_directory = Path(__file__).parents[2] / session["raw_directory"]
    transcript = (raw_directory / "stdout.jsonl").read_text(encoding="utf-8")
    assert session["observed_skill_read"] is True
    assert real_codex_audit["skill"]["name"].casefold() in transcript.casefold()
    assert "skill.md" in transcript.casefold()
    assert real_codex_audit["skill"]["invocation_fingerprint"] in session["last_message"]


def test_codex_trigger_precision(real_codex_audit) -> None:  # type: ignore[no-untyped-def]
    sessions = real_codex_audit["sessions"]
    assert sessions["negative"]["observed_skill_read"] is False
    assert real_codex_audit["profile"] == "explicit_only"
    assert real_codex_audit["implicit_metrics_claimed"] is False
    assert real_codex_audit["exposure"]["implicit_invocation"] is False


def test_codex_bad_description_block(real_codex_audit) -> None:  # type: ignore[no-untyped-def]
    session = real_codex_audit["sessions"]["bad_description"]
    assert session["exit_code"] == 0
    assert session["decoy_observed"] is False
    assert real_codex_audit["checks"]["bad_description_block"] is True
