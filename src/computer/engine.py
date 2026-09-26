"""Choose HTTP vs optional Playwright for public-web computer use."""

from __future__ import annotations

import os
from typing import Callable

import requests

from src.computer.browse import Page, UnsafeURLError, fetch_page
from src.computer.playwright_fetch import (
    PlaywrightUnavailableError,
    fetch_page_playwright,
    playwright_available,
)

EMPTY_TEXT_CHARS = 40
THIN_TEXT_CHARS = 200
JS_SHELL_MARKERS = (
    "enable javascript",
    "enable js",
    "javascript is required",
    "javascript required",
    "please enable javascript",
    "you need to enable javascript",
    "this site requires javascript",
)
_THIN_LOADING_MARKERS = (
    "loading",
    "please wait",
    "spinner",
)

OpenPage = Callable[..., Page]


def browser_mode() -> str:
    """``auto`` (default), ``http``, or ``playwright`` from ``JARVIS_BROWSER``."""
    raw = (os.getenv("JARVIS_BROWSER") or "auto").strip().lower()
    if raw in {"auto", "http", "playwright"}:
        return raw
    return "auto"


def looks_like_js_shell(page: Page) -> bool:
    """True when static HTML looks empty or like a JS-only shell.

    Short real pages (example.com) stay on HTTP. Empty documents and
    explicit "enable JavaScript" shells retry with Playwright.
    """
    text = (page.text or "").strip()
    title = (page.title or "").strip().lower()
    blob = f"{title} {text.lower()}"
    if any(marker in blob for marker in JS_SHELL_MARKERS):
        return True
    if len(text) < EMPTY_TEXT_CHARS:
        return True
    if len(text) < THIN_TEXT_CHARS and any(
        marker in blob for marker in _THIN_LOADING_MARKERS
    ):
        return True
    return False


def open_public_page(
    url: str,
    session: requests.Session | None = None,
    *,
    resolver=None,
    playwright_fetch: OpenPage | None = None,
    playwright_ready: bool | None = None,
) -> Page:
    """Open a public page. HTTP first; Playwright only if it would help.

    Still one AI key. Private/localhost targets are never sent to
    Playwright. Missing Playwright falls back to the HTTP result.
    """
    mode = browser_mode()
    ready = playwright_available() if playwright_ready is None else bool(playwright_ready)
    fetcher = playwright_fetch or fetch_page_playwright

    http_page: Page | None = None
    http_error: UnsafeURLError | None = None
    if mode in {"auto", "http"}:
        try:
            http_page = fetch_page(url, session=session, resolver=resolver)
        except UnsafeURLError as exc:
            http_error = exc
            if mode == "http" or not exc.retryable:
                raise

    if mode == "http":
        assert http_page is not None
        return http_page

    if mode == "auto" and http_page is not None and not looks_like_js_shell(http_page):
        return http_page

    if not ready and playwright_fetch is None:
        if http_page is not None:
            return http_page
        if http_error is not None:
            raise http_error
        raise PlaywrightUnavailableError()

    try:
        js_page = fetcher(url, resolver=resolver)
    except TypeError:
        js_page = fetcher(url)
    except PlaywrightUnavailableError:
        if http_page is not None:
            return http_page
        if http_error is not None:
            raise http_error
        raise
    except UnsafeURLError:
        if http_page is not None:
            return http_page
        raise

    if http_page is not None and len(js_page.text or "") <= len(http_page.text or ""):
        return http_page
    return js_page


def browser_status() -> dict:
    """Status payload for ``--status`` and the localhost UI."""
    mode = browser_mode()
    installed = playwright_available()
    if mode == "http":
        backend = "http"
        summary = (
            "Browser: public HTTP pages (Playwright disabled via JARVIS_BROWSER=http)"
        )
    elif mode == "playwright" and not installed:
        backend = "playwright-unavailable"
        summary = (
            "Browser: Playwright requested but not installed "
            "(pip install playwright && playwright install chromium)"
        )
    elif installed:
        backend = "playwright"
        if mode == "playwright":
            summary = "Browser: Playwright for public pages (JS-capable, no extra API key)"
        else:
            summary = (
                "Browser: public HTTP pages; Playwright ready for JS-heavy sites "
                "(no extra API key)"
            )
    else:
        backend = "http"
        summary = (
            "Browser: public HTTP pages "
            "(optional: pip install playwright && playwright install chromium)"
        )
    return {
        "mode": mode,
        "backend": backend,
        "playwright": installed,
        "summary": summary,
    }


def browser_status_line() -> str:
    return str(browser_status()["summary"])
