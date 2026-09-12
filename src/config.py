"""Resolve LLM provider(s) from environment variables.

Jarvis is designed so you drop in one AI API key and the built-in tools
(weather, time, research, chat, memory, automations, computer/browser)
work without extra vendor accounts. A localhost web UI (`--serve`) uses
that same key. Mail and calendar use optional Google OAuth — connect via
auth, not a second AI key. Optional MCP servers add third-party tools
the same way — still no second AI key.

A second Gemini/OpenAI/Anthropic key is optional. If present, Jarvis can
fail over to it when the primary provider is down — still no tool keys.
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

USER_AGENT = "JarvisPersonalAssistant/0.9 (+https://github.com/KrishMay05/Jarvis)"


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
    """Pick the primary LLM provider from env.

    One key is enough. Extra provider keys become backups (see
    ``list_llm_settings``).

    Priority:
    1. ``JARVIS_LLM_PROVIDER`` plus that provider's key (or ``JARVIS_API_KEY``)
    2. The first standard key found: Gemini, OpenAI, then Anthropic
    3. ``JARVIS_API_KEY`` with a provider inferred from the key prefix
    """
    return list_llm_settings()[0]


def list_llm_settings() -> list[LLMSettings]:
    """Configured providers, preferred first.

    Still works with a single key. Additional Gemini / OpenAI / Anthropic
    keys are optional backups, not extra vendor accounts for tools.
    """
    explicit = (os.getenv("JARVIS_LLM_PROVIDER") or "").strip().lower()
    if explicit and explicit not in SUPPORTED_PROVIDERS:
        raise RuntimeError(
            f"Unknown JARVIS_LLM_PROVIDER '{explicit}'. "
            f"Use one of: {', '.join(SUPPORTED_PROVIDERS)}."
        )

    ordered: list[LLMSettings] = []
    seen: set[str] = set()

    def _add(provider: str, api_key: str, *, allow_override: bool) -> None:
        key = (api_key or "").strip()
        if not key or provider in seen:
            return
        seen.add(provider)
        ordered.append(
            LLMSettings(
                provider=provider,
                api_key=key,
                model=_model_for(provider, allow_override=allow_override),
            )
        )

    if explicit:
        api_key = _key_for_provider(explicit)
        if not api_key:
            raise MissingAPIKeyError(_missing_key_message(explicit))
        _add(explicit, api_key, allow_override=True)

    for name in SUPPORTED_PROVIDERS:
        _add(
            name,
            _first_env(_PROVIDER_KEY_VARS[name]),
            allow_override=not ordered,
        )

    shared = (os.getenv("JARVIS_API_KEY") or "").strip()
    if shared:
        inferred = _infer_provider(shared)
        _add(inferred, shared, allow_override=not ordered)

    if not ordered:
        raise MissingAPIKeyError(_missing_key_message(None))
    return ordered


def describe_runtime(settings: LLMSettings | None = None) -> str:
    """Human-readable setup summary for --status and the REPL banner."""
    from src.auth.store import auth_status_line
    from src.automation.store import automation_status_line
    from src.mcp.config import mcp_status_line
    from src.memory.store import memory_status_line

    configured: list[LLMSettings] = []
    try:
        configured = list_llm_settings()
    except MissingAPIKeyError:
        if settings is None:
            raise
    settings = settings or configured[0]
    fallbacks = [item for item in configured if item.provider != settings.provider]
    fallback_line = ""
    if fallbacks:
        names = ", ".join(item.summary() for item in fallbacks)
        fallback_line = (
            f"\nFallback LLM: {names} "
            "(optional extra key — used only if the primary provider fails)"
        )
    return (
        f"LLM: {settings.summary()}{fallback_line}\n"
        "Tools: weather (wttr.in), time (local clock), "
        "research (Wikipedia + public web), chat (your LLM), "
        "memory (local file), automations (local schedule), "
        "computer (public web pages), mail/calendar (Google OAuth), "
        "web UI (localhost --serve; Connect Google in the sidebar)\n"
        f"{mcp_status_line()}\n"
        f"{memory_status_line()}\n"
        f"{automation_status_line()}\n"
        f"{auth_status_line()}\n"
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


def _model_for(provider: str, *, allow_override: bool = True) -> str:
    if allow_override:
        override = (os.getenv("JARVIS_MODEL") or "").strip()
        if override:
            return override
    return _DEFAULT_MODELS[provider]


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
