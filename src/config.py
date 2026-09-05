"""Resolve a single LLM provider from environment variables.

Jarvis is designed so you drop in one AI API key and the built-in tools
(weather, time, research, chat, memory) work without extra vendor accounts.
Optional MCP servers add third-party tools the same way — still no second AI key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


SUPPORTED_PROVIDERS = ("gemini", "openai", "anthropic")

_DEFAULT_MODELS = {
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
}

_PROVIDER_KEY_VARS = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
}

_KEY_PREFIXES = (
    ("sk-ant-", "anthropic"),
    ("sk-", "openai"),
    ("AIza", "gemini"),
)

USER_AGENT = "JarvisPersonalAssistant/0.5 (+https://github.com/KrishMay05/Jarvis)"


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str
    model: str

    def summary(self) -> str:
        return f"{self.provider} ({self.model})"


class MissingAPIKeyError(RuntimeError):
    """Raised when no supported LLM API key is configured."""


def get_llm_settings() -> LLMSettings:
    """Pick the LLM provider from env.

    Priority:
    1. ``JARVIS_LLM_PROVIDER`` plus that provider's key (or ``JARVIS_API_KEY``)
    2. The first standard key found: Gemini, OpenAI, then Anthropic
    3. ``JARVIS_API_KEY`` with a provider inferred from the key prefix
    """
    provider = (os.getenv("JARVIS_LLM_PROVIDER") or "").strip().lower()
    if provider:
        if provider not in SUPPORTED_PROVIDERS:
            raise RuntimeError(
                f"Unknown JARVIS_LLM_PROVIDER '{provider}'. "
                f"Use one of: {', '.join(SUPPORTED_PROVIDERS)}."
            )
        api_key = _key_for_provider(provider)
        if not api_key:
            raise MissingAPIKeyError(_missing_key_message(provider))
        return LLMSettings(
            provider=provider, api_key=api_key, model=_model_for(provider)
        )

    for name in SUPPORTED_PROVIDERS:
        api_key = _first_env(_PROVIDER_KEY_VARS[name])
        if api_key:
            return LLMSettings(provider=name, api_key=api_key, model=_model_for(name))

    shared = (os.getenv("JARVIS_API_KEY") or "").strip()
    if shared:
        inferred = _infer_provider(shared)
        return LLMSettings(
            provider=inferred, api_key=shared, model=_model_for(inferred)
        )

    raise MissingAPIKeyError(_missing_key_message(None))


def describe_runtime(settings: LLMSettings | None = None) -> str:
    """Human-readable setup summary for --status and the REPL banner."""
    from src.mcp.config import mcp_status_line
    from src.memory.store import memory_status_line

    settings = settings or get_llm_settings()
    return (
        f"LLM: {settings.summary()}\n"
        "Tools: weather (wttr.in), time (local clock), "
        "research (Wikipedia + DuckDuckGo), chat (your LLM), "
        "memory (local file)\n"
        f"{mcp_status_line()}\n"
        f"{memory_status_line()}\n"
        "Extra API keys: none required for built-in tools"
    )


def _key_for_provider(provider: str) -> str:
    for var in _PROVIDER_KEY_VARS[provider]:
        value = (os.getenv(var) or "").strip()
        if value:
            return value
    return (os.getenv("JARVIS_API_KEY") or "").strip()


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _model_for(provider: str) -> str:
    override = (os.getenv("JARVIS_MODEL") or "").strip()
    return override or _DEFAULT_MODELS[provider]


def _infer_provider(api_key: str) -> str:
    for prefix, provider in _KEY_PREFIXES:
        if api_key.startswith(prefix):
            return provider
    return "gemini"


def _missing_key_message(provider: str | None) -> str:
    if provider:
        names = list(_PROVIDER_KEY_VARS[provider]) + ["JARVIS_API_KEY"]
        return f"No API key found for {provider}. Set one of: {', '.join(names)}."
    return (
        "No AI API key found. Drop one key into .env and Jarvis will use it:\n"
        "  GEMINI_API_KEY=...\n"
        "  OPENAI_API_KEY=...\n"
        "  ANTHROPIC_API_KEY=...\n"
        "or JARVIS_API_KEY=... with JARVIS_LLM_PROVIDER=gemini|openai|anthropic"
    )
