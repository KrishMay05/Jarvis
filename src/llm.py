"""Gemini client used by agents and the orchestrator."""

from __future__ import annotations

import os

from google import genai
from google.genai import types

_client: genai.Client | None = None
_configured_key: str | None = None

_SYSTEM_INSTRUCTION = (
    "You are a helpful AI assistant similar to Jarvis from Iron Man. "
    "You should format your responses with swagger and confidence, "
    "similar to Jarvis. Answer concisely. When a JSON response format "
    "is requested, return only valid JSON."
)


def _get_client() -> genai.Client:
    global _client, _configured_key
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    if _client is None or _configured_key != api_key:
        _client = genai.Client(api_key=api_key)
        _configured_key = api_key
    return _client


def query_llm(prompt: str, model: str = "gemini-2.0-flash") -> str:
    """Send `prompt` to Google Gemini and return the response text."""
    print(f">>> Prompt:\n{prompt}\n")
    response = _get_client().models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=_SYSTEM_INSTRUCTION),
    )
    final_response = (response.text or "").strip()
    print(f"<<< Response:\n{final_response}\n")
    return final_response
