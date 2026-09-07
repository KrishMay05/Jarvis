"""In-process browser tab: open a page, then follow its links."""

from __future__ import annotations

import socket
from typing import Callable

import requests

from src.computer.browse import (
    Page,
    UnsafeURLError,
    fetch_page,
    normalize_url,
)
from src.config import USER_AGENT


class BrowserSession:
    """One REPL-wide public-web tab. No extra API key, no headless browser."""

    def __init__(
        self,
        session: requests.Session | None = None,
        resolver: Callable | None = None,
    ):
        self.http = session or requests.Session()
        self.http.headers.setdefault("User-Agent", USER_AGENT)
        self.resolver = resolver or socket.getaddrinfo
        self.last_page: Page | None = None

    def open(self, url: str) -> str:
        try:
            page = fetch_page(url, session=self.http, resolver=self.resolver)
        except UnsafeURLError as exc:
            return str(exc)
        self.last_page = page
        return page.format()

    def follow(self, query: str) -> str:
        page = self.last_page
        if page is None:
            return "Open a page first, then follow a link by its text or URL."
        target = _match_link(page.links, query)
        if not target:
            if not page.links:
                return f"No links on {page.url} to follow."
            return (
                f"No link matched '{query}'. Available links:\n"
                + "\n".join(f"- {label}: {href}" for label, href in page.links)
            )
        return self.open(target)

    def list_links(self) -> str:
        page = self.last_page
        if page is None:
            return "Open a page first to list its links."
        if not page.links:
            return f"No links found on {page.url}."
        lines = [f"Links on {page.title or page.url}:"]
        lines.extend(f"- {label}: {href}" for label, href in page.links)
        return "\n".join(lines)

    def current(self) -> str:
        if self.last_page is None:
            return "No page is open yet."
        return self.last_page.format()


def _match_link(links: list[tuple[str, str]], query: str) -> str | None:
    needle = (query or "").strip()
    if not needle:
        return None
    try:
        needle_url = normalize_url(needle)
    except UnsafeURLError:
        needle_url = ""
    lowered = needle.lower()
    for label, href in links:
        if needle_url and href.rstrip("/") == needle_url.rstrip("/"):
            return href
        if lowered in label.lower() or lowered in href.lower():
            return href
    return None
