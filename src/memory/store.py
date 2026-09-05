"""Local persistent memory — no extra API key required.

Facts the user asked Jarvis to keep, plus recent conversation turns, live in
a JSON file under ``~/.jarvis`` (or ``JARVIS_HOME`` / ``JARVIS_MEMORY_PATH``).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA_VERSION = 1
_MAX_FACTS = 200
_MAX_TURNS = 40
_MAX_FACT_CHARS = 500
_MAX_TURN_CHARS = 2000


def jarvis_home() -> Path:
    """Directory for local assistant state (memory, optional mcp.json)."""
    override = (os.getenv("JARVIS_HOME") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".jarvis"


def default_memory_path() -> Path:
    explicit = (os.getenv("JARVIS_MEMORY_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return jarvis_home() / "memory.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clip(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


@dataclass
class MemoryFact:
    id: str
    text: str
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, raw: object) -> MemoryFact | None:
        if not isinstance(raw, dict):
            return None
        text = str(raw.get("text") or "").strip()
        ident = str(raw.get("id") or "").strip()
        if not text or not ident:
            return None
        created = str(raw.get("created_at") or "").strip() or _utc_now()
        return cls(id=ident, text=text, created_at=created)


@dataclass
class MemoryTurn:
    role: str
    text: str
    at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict:
        return {"role": self.role, "text": self.text, "at": self.at}

    @classmethod
    def from_dict(cls, raw: object) -> MemoryTurn | None:
        if not isinstance(raw, dict):
            return None
        role = str(raw.get("role") or "").strip().lower()
        text = str(raw.get("text") or "").strip()
        if role not in {"user", "assistant"} or not text:
            return None
        at = str(raw.get("at") or "").strip() or _utc_now()
        return cls(role=role, text=text, at=at)


class MemoryStore:
    """Load, mutate, and persist durable facts plus recent turns."""

    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else default_memory_path()
        self.facts: list[MemoryFact] = []
        self.turns: list[MemoryTurn] = []
        self.load()

    def load(self) -> None:
        self.facts = []
        self.turns = []
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        facts: list[MemoryFact] = []
        for item in raw.get("facts") or []:
            parsed = MemoryFact.from_dict(item)
            if parsed is not None:
                facts.append(parsed)
        turns: list[MemoryTurn] = []
        for item in raw.get("turns") or []:
            parsed = MemoryTurn.from_dict(item)
            if parsed is not None:
                turns.append(parsed)
        self.facts = facts[-_MAX_FACTS:]
        self.turns = turns[-_MAX_TURNS:]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)

    def to_dict(self) -> dict:
        return {
            "version": _SCHEMA_VERSION,
            "facts": [fact.to_dict() for fact in self.facts],
            "turns": [turn.to_dict() for turn in self.turns],
        }

    def remember(self, text: str) -> str:
        fact_text = _clip(str(text or ""), _MAX_FACT_CHARS)
        if not fact_text:
            return "Nothing to remember. Give me a short fact or preference."
        for existing in self.facts:
            if existing.text.casefold() == fact_text.casefold():
                return f"Already remembered ({existing.id}): {existing.text}"
        fact = MemoryFact(id=_new_id(), text=fact_text)
        self.facts.append(fact)
        self.facts = self.facts[-_MAX_FACTS:]
        self.save()
        return f"Remembered ({fact.id}): {fact.text}"

    def forget(self, query: str) -> str:
        needle = str(query or "").strip()
        if not needle:
            return "Say what to forget (a fact id or a word that matches it)."
        remaining: list[MemoryFact] = []
        removed: list[MemoryFact] = []
        for fact in self.facts:
            if _fact_matches(fact, needle):
                removed.append(fact)
            else:
                remaining.append(fact)
        if not removed:
            return f"No remembered fact matched '{needle}'."
        self.facts = remaining
        self.save()
        lines = [f"Forgot {len(removed)} fact(s):"]
        lines.extend(f"- ({fact.id}) {fact.text}" for fact in removed)
        return "\n".join(lines)

    def recall(self, query: str | None = None) -> str:
        needle = str(query or "").strip()
        facts = self.facts
        if needle:
            facts = [fact for fact in facts if _fact_matches(fact, needle)]
        if not facts:
            if needle:
                return f"Nothing remembered that matches '{needle}'."
            return "No durable memories yet. Ask me to remember a fact or preference."
        header = (
            f"Remembered facts matching '{needle}':"
            if needle
            else "Remembered facts:"
        )
        lines = [header]
        lines.extend(f"- ({fact.id}) {fact.text}" for fact in facts)
        return "\n".join(lines)

    def record_exchange(self, user_text: str, assistant_text: str) -> None:
        user = _clip(str(user_text or ""), _MAX_TURN_CHARS)
        assistant = _clip(str(assistant_text or ""), _MAX_TURN_CHARS)
        if user:
            self.turns.append(MemoryTurn(role="user", text=user))
        if assistant:
            self.turns.append(MemoryTurn(role="assistant", text=assistant))
        if not user and not assistant:
            return
        self.turns = self.turns[-_MAX_TURNS:]
        self.save()

    def prompt_context(self, *, max_facts: int = 20, max_turns: int = 8) -> str:
        """Compact block for LLM prompts. Empty string when nothing is stored."""
        sections: list[str] = []
        if self.facts:
            lines = ["Durable memories (keep using these unless the user updates them):"]
            for fact in self.facts[-max_facts:]:
                lines.append(f"- ({fact.id}) {fact.text}")
            sections.append("\n".join(lines))
        recent = self.turns[-max_turns:]
        if recent:
            lines = ["Recent conversation (persists across sessions):"]
            for turn in recent:
                speaker = "User" if turn.role == "user" else "Jarvis"
                lines.append(f"{speaker}: {turn.text}")
            sections.append("\n".join(lines))
        return "\n\n".join(sections)

    def status_line(self) -> str:
        fact_n = len(self.facts)
        turn_n = len(self.turns)
        location = self.path
        if fact_n == 0 and turn_n == 0:
            return (
                f"Memory: empty (facts persist at {location} — no extra API key)"
            )
        return (
            f"Memory: {fact_n} fact(s), {turn_n} turn(s) at {location}"
        )


def memory_status_line(store: MemoryStore | None = None) -> str:
    if store is not None:
        return store.status_line()
    path = default_memory_path()
    if not path.is_file():
        return f"Memory: empty (facts persist at {path} — no extra API key)"
    return MemoryStore(path).status_line()


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _fact_matches(fact: MemoryFact, query: str) -> bool:
    needle = query.casefold()
    if fact.id.casefold() == needle:
        return True
    if needle in fact.text.casefold():
        return True
    tokens = [token for token in re.split(r"\W+", needle) if token]
    if tokens and all(token in fact.text.casefold() for token in tokens):
        return True
    return False
