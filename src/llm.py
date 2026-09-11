"""Provider-agnostic LLM client used by agents and the orchestrator."""

from __future__ import annotations

import os

from src.config import LLMSettings, MissingAPIKeyError, get_llm_settings, list_llm_settings

_SYSTEM_INSTRUCTION = (
    "You are a helpful AI assistant similar to Jarvis from Iron Man. "
    "You should format your responses with swagger and confidence, "
    "similar to Jarvis. Answer concisely. When a JSON response format "
    "is requested, return only valid JSON."
)

_FALSEY_FAILOVER = frozenset({"0", "false", "no", "off", "disable", "disabled"})

# After a successful failover, keep using that provider for this process so
# a downed primary does not add latency on every orchestrator step.
_sticky_provider: str | None = None


def reset_failover_state() -> None:
    """Clear in-process failover stickiness (used by tests)."""
    global _sticky_provider
    _sticky_provider = None


def query_llm(prompt: str, model: str | None = None) -> str:
    """Send `prompt` to the configured provider and return the response text.

    One AI key is enough. If extra Gemini / OpenAI / Anthropic keys are set,
    a provider outage, rate limit, or auth failure fails over to the next
    key instead of taking the whole assistant down.
    """
    candidates = _configured_providers()
    if not _failover_enabled():
        candidates = candidates[:1]
    ordered = _order_candidates(candidates)
    primary = candidates[0]
    errors: list[str] = []
    last_exc: BaseException | None = None

    for settings in ordered:
        chosen_model = (
            model if (model and settings.provider == primary.provider) else settings.model
        )
        if os.getenv("JARVIS_DEBUG"):
            print(f">>> [{settings.provider}/{chosen_model}] Prompt:\n{prompt}\n")
        try:
            text = _dispatch(settings, prompt, chosen_model)
        except BaseException as exc:
            if not _is_failover_error(exc):
                raise
            last_exc = exc
            errors.append(f"{settings.provider}: {exc}")
            _forget_sticky(settings.provider)
            if os.getenv("JARVIS_DEBUG"):
                print(f"!!! {settings.provider} failed; trying the next configured key.\n")
            continue

        final_response = (text or "").strip()
        _remember_sticky(settings.provider)
        if os.getenv("JARVIS_DEBUG"):
            print(f"<<< Response:\n{final_response}\n")
        return final_response

    if last_exc is not None and len(candidates) < 2:
        raise last_exc
    detail = "\n".join(f"  {item}" for item in errors) or "  (no extra key configured)"
    raise RuntimeError(
        "All configured LLM providers failed. Jarvis still only needs one "
        "working AI key; extra keys are optional backups.\n" + detail
    ) from last_exc


def _configured_providers() -> list[LLMSettings]:
    try:
        return list_llm_settings()
    except MissingAPIKeyError:
        # Tests (and callers) may stub get_llm_settings without env keys.
        return [get_llm_settings()]


def _failover_enabled() -> bool:
    raw = (os.getenv("JARVIS_LLM_FAILOVER") or "1").strip().lower()
    return raw not in _FALSEY_FAILOVER


def _order_candidates(candidates: list[LLMSettings]) -> list[LLMSettings]:
    if not _sticky_provider:
        return list(candidates)
    preferred = [item for item in candidates if item.provider == _sticky_provider]
    rest = [item for item in candidates if item.provider != _sticky_provider]
    return preferred + rest


def _remember_sticky(provider: str) -> None:
    global _sticky_provider
    _sticky_provider = provider


def _forget_sticky(provider: str) -> None:
    global _sticky_provider
    if _sticky_provider == provider:
        _sticky_provider = None


def _is_failover_error(exc: BaseException) -> bool:
    if isinstance(exc, (KeyboardInterrupt, SystemExit, MissingAPIKeyError)):
        return False
    return isinstance(exc, Exception)


def _dispatch(settings: LLMSettings, prompt: str, model: str) -> str:
    if settings.provider == "gemini":
        return _query_gemini(settings, prompt, model)
    if settings.provider == "openai":
        return _query_openai(settings, prompt, model)
    if settings.provider == "anthropic":
        return _query_anthropic(settings, prompt, model)
    raise RuntimeError(f"Unsupported LLM provider: {settings.provider}")


def _query_gemini(settings: LLMSettings, prompt: str, model: str) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "The google-genai package is required for Gemini. "
            "Run: pip install -r requirements.txt"
        ) from exc

    client = genai.Client(api_key=settings.api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=_SYSTEM_INSTRUCTION),
    )
    return response.text or ""


def _query_openai(settings: LLMSettings, prompt: str, model: str) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The openai package is required for OpenAI. "
            "Run: pip install -r requirements.txt"
        ) from exc

    client = OpenAI(api_key=settings.api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
    )
    message = response.choices[0].message
    return message.content or ""


def _query_anthropic(settings: LLMSettings, prompt: str, model: str) -> str:
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The anthropic package is required for Anthropic. "
            "Run: pip install -r requirements.txt"
        ) from exc

    client = Anthropic(api_key=settings.api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=_SYSTEM_INSTRUCTION,
        messages=[{"role": "user", "content": prompt}],
    )
    parts: list[str] = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


# Re-export so callers can catch a missing key from either module.
__all__ = ["query_llm", "MissingAPIKeyError", "reset_failover_state"]
