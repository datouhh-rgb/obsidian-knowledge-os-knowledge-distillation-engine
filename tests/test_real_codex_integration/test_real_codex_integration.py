from __future__ import annotations


def test_codex_skill_exposure(real_codex_audit) -> None:  # type: ignore[no-untyped-def]
    exposure = real_codex_audit["exposure"]
    assert exposure["mechanism"] == "managed-directory-link"
    assert exposure["copied_skill_file"] is False
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
    assert real_codex_audit["official_skill_contract"] == "https://learn.chatgpt.com/docs/build-skills"


def test_codex_explicit_skill_call(real_codex_audit) -> None:  # type: ignore[no-untyped-def]
    session = real_codex_audit["sessions"]["explicit"]
    assert session["exit_code"] == 0
    assert session["observed_skill_read"] is True
    assert session["detection"] == "raw-codex-event-read-target-skill"
    assert real_codex_audit["skill"]["invocation_fingerprint"] in session["last_message"]
    assert real_codex_audit["review"]["automatic"] is False
    assert real_codex_audit["review"]["before_status"] == "candidate"
    assert real_codex_audit["review"]["after_status"] == "approved"


def test_codex_trigger_precision(real_codex_audit) -> None:  # type: ignore[no-untyped-def]
    sessions = real_codex_audit["sessions"]
    assert sessions["trigger_positive"]["observed_skill_read"] is True
    assert sessions["trigger_negative"]["observed_skill_read"] is False
    evaluation = real_codex_audit["trigger_evaluation"]
    assert evaluation["case_count"] == 25
    assert evaluation["counts"] == {"positive": 10, "negative": 10, "boundary": 5}
    assert evaluation["passed"] is True
    assert all(value >= 80 for value in evaluation["metrics"].values())
    assert real_codex_audit["exposure"]["implicit_invocation"] is False


def test_codex_bad_description_block(real_codex_audit) -> None:  # type: ignore[no-untyped-def]
    session = real_codex_audit["sessions"]["bad_description"]
    assert session["exit_code"] == 0
    assert session["decoy_observed"] is False
    assert real_codex_audit["checks"]["bad_description_block"] is True
