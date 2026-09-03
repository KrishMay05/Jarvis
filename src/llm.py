"""Provider-agnostic LLM client used by agents and the orchestrator."""

from __future__ import annotations

import os

from src.config import LLMSettings, MissingAPIKeyError, get_llm_settings

_SYSTEM_INSTRUCTION = (
    "You are a helpful AI assistant similar to Jarvis from Iron Man. "
    "You should format your responses with swagger and confidence, "
    "similar to Jarvis. Answer concisely. When a JSON response format "
    "is requested, return only valid JSON."
)


def query_llm(prompt: str, model: str | None = None) -> str:
    """Send `prompt` to the configured provider and return the response text."""
    settings = get_llm_settings()
    chosen_model = model or settings.model
    if os.getenv("JARVIS_DEBUG"):
        print(f">>> [{settings.provider}/{chosen_model}] Prompt:\n{prompt}\n")

    if settings.provider == "gemini":
        text = _query_gemini(settings, prompt, chosen_model)
    elif settings.provider == "openai":
        text = _query_openai(settings, prompt, chosen_model)
    elif settings.provider == "anthropic":
        text = _query_anthropic(settings, prompt, chosen_model)
    else:
        raise RuntimeError(f"Unsupported LLM provider: {settings.provider}")

    final_response = (text or "").strip()
    if os.getenv("JARVIS_DEBUG"):
        print(f"<<< Response:\n{final_response}\n")
    return final_response


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
__all__ = ["query_llm", "MissingAPIKeyError"]
