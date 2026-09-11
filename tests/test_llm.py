from unittest.mock import Mock

import pytest

from src.config import LLMSettings
from src.llm import _query_anthropic, _query_gemini, _query_openai, query_llm, reset_failover_state


def test_query_llm_dispatches_to_openai(monkeypatch):
    monkeypatch.setattr(
        "src.llm.get_llm_settings",
        lambda: LLMSettings(provider="openai", api_key="sk-test", model="gpt-4o-mini"),
    )
    monkeypatch.setattr(
        "src.llm._query_openai",
        lambda settings, prompt, model: f"openai:{prompt}:{model}",
    )
    assert query_llm("ping") == "openai:ping:gpt-4o-mini"


def test_query_llm_dispatches_to_anthropic(monkeypatch):
    monkeypatch.setattr(
        "src.llm.get_llm_settings",
        lambda: LLMSettings(
            provider="anthropic", api_key="sk-ant-test", model="claude-sonnet-4-5"
        ),
    )
    monkeypatch.setattr(
        "src.llm._query_anthropic",
        lambda settings, prompt, model: f"anthropic:{prompt}",
    )
    assert query_llm("ping") == "anthropic:ping"


def test_query_llm_dispatches_to_gemini(monkeypatch):
    monkeypatch.setattr(
        "src.llm.get_llm_settings",
        lambda: LLMSettings(
            provider="gemini", api_key="gemini-test", model="gemini-2.0-flash"
        ),
    )
    monkeypatch.setattr(
        "src.llm._query_gemini",
        lambda settings, prompt, model: f"gemini:{prompt}",
    )
    assert query_llm("ping") == "gemini:ping"


def test_query_openai_sends_chat_completion(monkeypatch):
    choice = Mock()
    choice.message.content = "  hello from openai  "
    completion = Mock()
    completion.choices = [choice]
    client = Mock()
    client.chat.completions.create.return_value = completion
    openai_mod = Mock()
    openai_mod.OpenAI.return_value = client
    monkeypatch.setattr("src.llm.OpenAI", openai_mod.OpenAI, raising=False)

    # Import happens inside the function; stub the module.
    import sys

    monkeypatch.setitem(sys.modules, "openai", openai_mod)

    settings = LLMSettings(provider="openai", api_key="sk-test", model="gpt-4o-mini")
    result = _query_openai(settings, "ping", "gpt-4o-mini")
    assert result == "  hello from openai  "
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["messages"][1]["content"] == "ping"


def test_query_anthropic_sends_messages(monkeypatch):
    block = Mock()
    block.text = "hello from claude"
    response = Mock()
    response.content = [block]
    client = Mock()
    client.messages.create.return_value = response
    anthropic_mod = Mock()
    anthropic_mod.Anthropic.return_value = client
    import sys

    monkeypatch.setitem(sys.modules, "anthropic", anthropic_mod)

    settings = LLMSettings(
        provider="anthropic", api_key="sk-ant-test", model="claude-sonnet-4-5"
    )
    result = _query_anthropic(settings, "ping", "claude-sonnet-4-5")
    assert result == "hello from claude"
    client.messages.create.assert_called_once()


def test_query_gemini_sends_generate_content(monkeypatch):
    response = Mock()
    response.text = "hello from gemini"
    models = Mock()
    models.generate_content.return_value = response
    client = Mock()
    client.models = models
    genai_mod = Mock()
    genai_mod.Client.return_value = client
    types_mod = Mock()
    types_mod.GenerateContentConfig = Mock(return_value="config")
    genai_mod.types = types_mod

    import sys
    import types as std_types

    google_pkg = std_types.ModuleType("google")
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)

    settings = LLMSettings(
        provider="gemini", api_key="gemini-test", model="gemini-2.0-flash"
    )
    result = _query_gemini(settings, "ping", "gemini-2.0-flash")
    assert result == "hello from gemini"
    models.generate_content.assert_called_once()


def test_query_llm_fails_over_to_backup_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-dead")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-backup")
    calls: list[str] = []

    def boom(settings, prompt, model):
        calls.append(f"gemini:{model}")
        raise RuntimeError("429 rate limited")

    def ok(settings, prompt, model):
        calls.append(f"openai:{model}")
        return f"recovered:{prompt}"

    monkeypatch.setattr("src.llm._query_gemini", boom)
    monkeypatch.setattr("src.llm._query_openai", ok)
    assert query_llm("ping") == "recovered:ping"
    assert calls == ["gemini:gemini-2.0-flash", "openai:gpt-4o-mini"]


def test_query_llm_uses_backup_model_not_primary_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-dead")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-backup")
    seen: list[str] = []

    def boom(settings, prompt, model):
        raise RuntimeError("overloaded")

    def ok(settings, prompt, model):
        seen.append(model)
        return "ok"

    monkeypatch.setattr("src.llm._query_gemini", boom)
    monkeypatch.setattr("src.llm._query_openai", ok)
    assert query_llm("ping", model="gemini-2.5-flash") == "ok"
    assert seen == ["gpt-4o-mini"]


def test_query_llm_sticky_prefers_last_working_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-flaky")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-backup")
    gemini_calls = {"n": 0}
    openai_calls = {"n": 0}

    def boom(settings, prompt, model):
        gemini_calls["n"] += 1
        raise RuntimeError("503 unavailable")

    def ok(settings, prompt, model):
        openai_calls["n"] += 1
        return "ok"

    monkeypatch.setattr("src.llm._query_gemini", boom)
    monkeypatch.setattr("src.llm._query_openai", ok)
    assert query_llm("one") == "ok"
    assert query_llm("two") == "ok"
    assert gemini_calls["n"] == 1
    assert openai_calls["n"] == 2
    reset_failover_state()


def test_query_llm_does_not_failover_when_disabled(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-dead")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-backup")
    monkeypatch.setenv("JARVIS_LLM_FAILOVER", "0")
    openai_calls = {"n": 0}

    def boom(settings, prompt, model):
        raise RuntimeError("quota")

    def ok(settings, prompt, model):
        openai_calls["n"] += 1
        return "should-not-run"

    monkeypatch.setattr("src.llm._query_gemini", boom)
    monkeypatch.setattr("src.llm._query_openai", ok)
    with pytest.raises(RuntimeError, match="quota"):
        query_llm("ping")
    assert openai_calls["n"] == 0


def test_query_llm_reports_every_provider_when_all_fail(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-dead")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dead")

    monkeypatch.setattr(
        "src.llm._query_gemini",
        lambda settings, prompt, model: (_ for _ in ()).throw(RuntimeError("gemini down")),
    )
    monkeypatch.setattr(
        "src.llm._query_openai",
        lambda settings, prompt, model: (_ for _ in ()).throw(RuntimeError("openai down")),
    )
    with pytest.raises(RuntimeError, match="All configured LLM providers failed") as info:
        query_llm("ping")
    message = str(info.value)
    assert "gemini: gemini down" in message
    assert "openai: openai down" in message


def test_query_llm_single_key_still_raises_original_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-only")

    def boom(settings, prompt, model):
        raise RuntimeError("invalid api key")

    monkeypatch.setattr("src.llm._query_openai", boom)
    with pytest.raises(RuntimeError, match="invalid api key"):
        query_llm("ping")
