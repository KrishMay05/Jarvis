"""Read Gmail through a connected Google account — OAuth, not an extra AI key."""

from __future__ import annotations

from src.auth.google import list_mail
from src.auth.store import AuthStore
from src.tools.base_tool import Tool

_LIST = frozenset(
    {"list", "inbox", "mail", "recent", "unread", "show", "ls", "status"}
)
_SEARCH = frozenset({"search", "find", "query", "from", "about"})


class MailTool(Tool):
    def __init__(self, store: AuthStore | None = None, http=None):
        self.store = store or AuthStore()
        self.http = http

    def name(self) -> str:
        return "mail"

    def aliases(self):
        return ("gmail", "inbox", "email", "emails")

    def description(self) -> str:
        return (
            "Read recent Gmail (readonly) after the user connects Google with "
            "`python main.py --connect google`. OAuth — not a second AI key. "
            "Args: inbox; unread; search from:ada; list."
        )

    def use(self, args) -> str:
        action, payload, extra = _parse_args(args)
        query = str(
            extra.get("q")
            or extra.get("query")
            or extra.get("search")
            or payload
            or ""
        ).strip()
        if action in _SEARCH:
            query = query or "in:inbox"
        elif action == "unread":
            query = "in:inbox is:unread"
        elif action in _LIST and not query:
            query = "in:inbox"
        elif query.lower() in {"unread", "is:unread"}:
            query = "in:inbox is:unread"
        try:
            limit = int(extra.get("limit") or extra.get("max") or 5)
        except (TypeError, ValueError):
            limit = 5
        return list_mail(self.store, query, limit=limit, http=self.http)


def _parse_args(args) -> tuple[str, str, dict]:
    if args is None:
        return "inbox", "", {}
    if isinstance(args, dict):
        extra = dict(args)
        action = str(
            extra.get("action")
            or extra.get("op")
            or extra.get("command")
            or ""
        ).strip().lower()
        payload = (
            extra.get("query")
            or extra.get("q")
            or extra.get("search")
            or extra.get("text")
            or extra.get("input")
            or extra.get("args")
            or ""
        )
        payload_text = str(payload).strip()
        if not action and payload_text:
            return _split_command(payload_text) + (extra,)
        if not action:
            return "inbox", payload_text, extra
        return action, payload_text, extra
    return _split_command(str(args).strip()) + ({},)


def _split_command(text: str) -> tuple[str, str]:
    if not text:
        return "inbox", ""
    first, _, rest = text.partition(" ")
    verb = first.strip().lower().rstrip(":")
    if verb in _LIST | _SEARCH:
        return verb, rest.strip()
    return "search", text
