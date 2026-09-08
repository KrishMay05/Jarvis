"""Read Google Calendar through a connected account — OAuth, not an extra AI key."""

from __future__ import annotations

from src.auth.google import list_events
from src.auth.store import AuthStore
from src.tools.base_tool import Tool

_LIST = frozenset(
    {
        "list",
        "upcoming",
        "events",
        "today",
        "calendar",
        "agenda",
        "show",
        "ls",
        "status",
        "schedule",
    }
)
_SEARCH = frozenset({"search", "find", "query", "about"})


class CalendarTool(Tool):
    def __init__(self, store: AuthStore | None = None, http=None):
        self.store = store or AuthStore()
        self.http = http

    def name(self) -> str:
        return "calendar"

    def aliases(self):
        return ("agenda", "events", "gcal", "schedule")

    def description(self) -> str:
        return (
            "Read upcoming Google Calendar events (readonly) after the user "
            "connects Google with `python main.py --connect google`. OAuth — "
            "not a second AI key. Args: upcoming; today; search standup."
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
        if action == "today" and not query:
            query = ""
        try:
            limit = int(extra.get("limit") or extra.get("max") or 8)
        except (TypeError, ValueError):
            limit = 8
        return list_events(self.store, query, limit=limit, http=self.http)


def _parse_args(args) -> tuple[str, str, dict]:
    if args is None:
        return "upcoming", "", {}
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
            return "upcoming", payload_text, extra
        return action, payload_text, extra
    return _split_command(str(args).strip()) + ({},)


def _split_command(text: str) -> tuple[str, str]:
    if not text:
        return "upcoming", ""
    first, _, rest = text.partition(" ")
    verb = first.strip().lower().rstrip(":")
    if verb in _LIST | _SEARCH:
        return verb, rest.strip()
    return "search", text
