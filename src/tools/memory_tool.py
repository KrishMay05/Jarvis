"""Remember / recall / forget personal facts — local file, no extra API key."""

from __future__ import annotations

from src.memory.store import MemoryStore
from src.tools.base_tool import Tool

_REMEMBER = frozenset({"remember", "save", "store", "add", "note"})
_RECALL = frozenset({"recall", "list", "show", "get", "search", "what"})
_FORGET = frozenset({"forget", "delete", "remove", "drop"})


class MemoryTool(Tool):
    def __init__(self, store: MemoryStore | None = None):
        self.store = store or MemoryStore()

    def name(self) -> str:
        return "memory"

    def aliases(self):
        return ("remember", "recall", "forget")

    def description(self) -> str:
        return (
            "Persist personal facts and preferences across sessions. "
            "Args: remember <fact>, recall [query], forget <query or id>, or list. "
            "No extra API key — stored locally."
        )

    def use(self, args) -> str:
        action, payload = _parse_args(args)
        if action in _REMEMBER:
            return self.store.remember(payload)
        if action in _FORGET:
            return self.store.forget(payload)
        if action in _RECALL:
            return self.store.recall(payload or None)
        return (
            "Use memory with remember <fact>, recall [query], forget <query>, or list."
        )


def _parse_args(args) -> tuple[str, str]:
    if args is None:
        return "recall", ""
    if isinstance(args, dict):
        action = str(
            args.get("action")
            or args.get("op")
            or args.get("command")
            or ""
        ).strip().lower()
        payload = (
            args.get("text")
            or args.get("fact")
            or args.get("query")
            or args.get("q")
            or args.get("input")
            or args.get("args")
            or args.get("id")
            or ""
        )
        payload_text = str(payload).strip()
        if not action and payload_text:
            return _split_command(payload_text)
        return action or "recall", payload_text
    return _split_command(str(args).strip())


def _split_command(text: str) -> tuple[str, str]:
    if not text:
        return "recall", ""
    first, _, rest = text.partition(" ")
    verb = first.strip().lower().rstrip(":")
    if verb in _REMEMBER | _RECALL | _FORGET:
        return verb, rest.strip()
    return "remember", text
