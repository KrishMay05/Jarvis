"""Helpers for turning messy LLM text into JSON."""

from __future__ import annotations

import json
import re
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def parse_llm_json(text: str) -> Any:
    """Parse a JSON object or array from an LLM response.

    Accepts raw JSON, optional markdown fences, and leading/trailing prose.
    Raises ValueError when no valid JSON object or array can be recovered.
    """
    if text is None:
        raise ValueError("Invalid JSON response: empty input")

    candidate = text.strip()
    if not candidate:
        raise ValueError("Invalid JSON response: empty input")

    fenced = _FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = _extract_json_blob(candidate)

    if isinstance(parsed, (dict, list)):
        return parsed

    raise ValueError("Invalid JSON response: expected object or array")


def _extract_json_blob(text: str) -> Any:
    decoder = json.JSONDecoder()
    for opener in ("{", "["):
        start = text.find(opener)
        if start == -1:
            continue
        try:
            parsed, _ = decoder.raw_decode(text[start:])
            return parsed
        except json.JSONDecodeError:
            continue
    raise ValueError("Invalid JSON response")
