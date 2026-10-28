"""Configuration tests: provider selection and documentation of every setting."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from annotator.ai.client import AIConfigError, ClaudeClient, make_client
from annotator.ai.fake import FakeClaude
from annotator.config import REPO_ROOT, Settings

# Variables that exist only for automated tests and are documented in docs/testing.md instead.
TEST_ONLY = {"ANNOTATOR_FAKE_DELAY_SECONDS"}


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **{k: str(v) for k, v in kw.items()})


def test_every_setting_is_documented_in_env_example():
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", example, re.M))
    aliases = {f.alias for f in Settings.model_fields.values() if f.alias}
    missing = sorted(aliases - documented)
    assert not missing, f"Add these to .env.example: {missing}"
    frontend = {"NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_API_PORT"}
    assert frontend <= documented


def test_env_variables_read_in_code_are_known():
    code = "\n".join(p.read_text(encoding="utf-8") for p in (REPO_ROOT / "backend" / "annotator").rglob("*.py"))
    read = set(re.findall(r'environ(?:\.get)?\(\s*"(ANNOTATOR_[A-Z_]+)"', code))
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    undocumented = sorted(v for v in read - TEST_ONLY if v not in example)
    assert not undocumented, f"Add these to .env.example: {undocumented}"


def test_provider_selection():
    assert isinstance(make_client(settings(ANNOTATOR_AI_PROVIDER="anthropic", ANTHROPIC_API_KEY="k")), ClaudeClient)
    assert isinstance(make_client(settings(ANNOTATOR_AI_PROVIDER="fake", ANNOTATOR_ENV="test")), FakeClaude)
    with pytest.raises(AIConfigError):  # scripted AI is refused outside tests
        make_client(settings(ANNOTATOR_AI_PROVIDER="fake", ANNOTATOR_ENV="development"))
    with pytest.raises(AIConfigError) as info:
        make_client(settings(ANNOTATOR_AI_PROVIDER="ollama"))
    assert "Unknown AI provider" in info.value.user_message


def test_ai_configured_matches_provider():
    assert not settings(ANNOTATOR_AI_PROVIDER="anthropic", ANTHROPIC_API_KEY="").ai_configured
    assert settings(ANNOTATOR_AI_PROVIDER="anthropic", ANTHROPIC_API_KEY="k").ai_configured
    assert not settings(ANNOTATOR_AI_PROVIDER="fake", ANNOTATOR_ENV="development", ANTHROPIC_API_KEY="k").ai_configured
    assert not settings(ANNOTATOR_AI_PROVIDER="something-else", ANTHROPIC_API_KEY="k").ai_configured


def test_shell_scripts_are_executable():
    for name in ("install.sh", "start.sh"):
        path = Path(REPO_ROOT / name)
        assert path.exists() and path.stat().st_mode & 0o111, f"{name} must be executable"
