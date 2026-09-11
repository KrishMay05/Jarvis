import pytest

from src.config import MissingAPIKeyError, describe_runtime, get_llm_settings, list_llm_settings


def test_detects_gemini_key(monkeypatch):
    monkeypatch.delenv("JARVIS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("JARVIS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")

    settings = get_llm_settings()
    assert settings.provider == "gemini"
    assert settings.api_key == "gemini-test-key"
    assert settings.model == "gemini-2.0-flash"


def test_detects_openai_when_no_gemini(monkeypatch):
    monkeypatch.delenv("JARVIS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("JARVIS_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

    settings = get_llm_settings()
    assert settings.provider == "openai"
    assert settings.model == "gpt-4o-mini"


def test_provider_override_uses_shared_key(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("JARVIS_API_KEY", "sk-ant-shared")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_MODEL", raising=False)

    settings = get_llm_settings()
    assert settings.provider == "anthropic"
    assert settings.api_key == "sk-ant-shared"
    assert settings.model == "claude-sonnet-4-5"


def test_infers_openai_from_jarvis_api_key_prefix(monkeypatch):
    for var in (
        "JARVIS_LLM_PROVIDER",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "JARVIS_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("JARVIS_API_KEY", "sk-abc123")

    settings = get_llm_settings()
    assert settings.provider == "openai"


def test_model_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("JARVIS_MODEL", "gemini-2.5-flash")
    monkeypatch.delenv("JARVIS_LLM_PROVIDER", raising=False)

    assert get_llm_settings().model == "gemini-2.5-flash"


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_PROVIDER", "mistral")
    monkeypatch.setenv("JARVIS_API_KEY", "x")
    with pytest.raises(RuntimeError, match="Unknown JARVIS_LLM_PROVIDER"):
        get_llm_settings()


def test_missing_key_raises(monkeypatch):
    for var in (
        "JARVIS_LLM_PROVIDER",
        "JARVIS_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(MissingAPIKeyError, match="No AI API key found"):
        get_llm_settings()


def test_describe_runtime_mentions_one_key_tools(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.delenv("JARVIS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("JARVIS_MODEL", raising=False)
    text = describe_runtime()
    assert "gemini" in text
    assert "wttr.in" in text
    assert "chat" in text.lower()
    assert "memory" in text.lower()
    assert "automation" in text.lower()
    assert "computer" in text.lower()
    assert "mail" in text.lower()
    assert "calendar" in text.lower()
    assert "web ui" in text.lower()
    assert "none required" in text.lower()
    assert "MCP:" in text
    assert "Memory:" in text
    assert "Automations:" in text
    assert "Auth:" in text


def test_list_llm_settings_one_key_is_enough(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-only")
    configured = list_llm_settings()
    assert [item.provider for item in configured] == ["openai"]
    assert get_llm_settings().provider == "openai"


def test_list_llm_settings_extra_keys_are_backups(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-primary")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-backup")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-backup")
    configured = list_llm_settings()
    assert [item.provider for item in configured] == ["gemini", "openai", "anthropic"]
    assert get_llm_settings().provider == "gemini"
    assert configured[1].model == "gpt-4o-mini"
    assert configured[2].model == "claude-sonnet-4-5"


def test_explicit_provider_stays_primary_when_other_keys_exist(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-primary")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-backup")
    configured = list_llm_settings()
    assert [item.provider for item in configured] == ["anthropic", "gemini"]


def test_model_override_applies_only_to_primary(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-primary")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-backup")
    monkeypatch.setenv("JARVIS_MODEL", "gemini-2.5-flash")
    configured = list_llm_settings()
    assert configured[0].model == "gemini-2.5-flash"
    assert configured[1].model == "gpt-4o-mini"


def test_shared_jarvis_api_key_does_not_duplicate_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("JARVIS_API_KEY", "sk-also-openai")
    configured = list_llm_settings()
    assert [item.provider for item in configured] == ["openai"]
    assert configured[0].api_key == "sk-openai"


def test_describe_runtime_mentions_optional_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-backup")
    text = describe_runtime()
    assert "Fallback LLM: openai (gpt-4o-mini)" in text
    assert "optional extra key" in text.lower()
    assert "none required" in text.lower()
