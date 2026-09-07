"""Open public web pages and follow links — no extra API key."""

from __future__ import annotations

from src.computer.session import BrowserSession
from src.tools.base_tool import Tool

_OPEN = frozenset({"open", "browse", "read", "fetch", "go", "visit", "load", "url"})
_FOLLOW = frozenset({"follow", "click", "open_link", "link"})
_LINKS = frozenset({"links", "list", "show"})
_CURRENT = frozenset({"current", "page", "this"})


class ComputerTool(Tool):
    def __init__(self, browser: BrowserSession | None = None):
        self.browser = browser or BrowserSession()

    def name(self) -> str:
        return "computer"

    def aliases(self):
        return ("browse", "browser", "open", "open_url")

    def description(self) -> str:
        return (
            "Computer use: open a public http(s) URL, read the page text, "
            "list links, or follow a link from the last page. Local HTTP only "
            "— no extra API key, no Playwright. "
            "Args: open https://example.com; follow Docs; links."
        )

    def use(self, args) -> str:
        action, payload, extra = _parse_args(args)
        url = str(extra.get("url") or extra.get("href") or "").strip()
        if action in _FOLLOW:
            return self.browser.follow(payload or url or str(extra.get("text") or ""))
        if action in _LINKS:
            return self.browser.list_links()
        if action in _CURRENT and not payload and not url:
            return self.browser.current()
        target = url or payload
        if action in _OPEN or target:
            if not target:
                return "Pass a public URL to open, for example https://example.com"
            return self.browser.open(target)
        return (
            "Use computer with open <url>, follow <link text>, links, or current."
        )


def _parse_args(args) -> tuple[str, str, dict]:
    if args is None:
        return "open", "", {}
    if isinstance(args, dict):
        extra = dict(args)
        action = str(
            extra.get("action")
            or extra.get("op")
            or extra.get("command")
            or ""
        ).strip().lower()
        payload = (
            extra.get("url")
            or extra.get("href")
            or extra.get("query")
            or extra.get("text")
            or extra.get("input")
            or extra.get("args")
            or extra.get("link")
            or ""
        )
        payload_text = str(payload).strip()
        if not action and payload_text:
            return _split_command(payload_text) + (extra,)
        if not action:
            return "open", payload_text, extra
        return action, payload_text, extra
    return _split_command(str(args).strip()) + ({},)


def _split_command(text: str) -> tuple[str, str]:
    if not text:
        return "open", ""
    first, _, rest = text.partition(" ")
    verb = first.strip().lower().rstrip(":")
    if verb in _OPEN | _FOLLOW | _LINKS | _CURRENT:
        return verb, rest.strip()
    return "open", text
