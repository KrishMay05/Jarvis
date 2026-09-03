from unittest.mock import Mock

from src.config import LLMSettings
from src.llm import _query_anthropic, _query_gemini, _query_openai, query_llm


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
