"""Optional Playwright fetch for JS-heavy public pages — no extra API key.

HTTP extraction stays the default. This module is imported only when
``JARVIS_BROWSER`` asks for Playwright or HTTP returned a thin JS shell.
``pip install playwright && playwright install chromium`` is enough;
there is no Browserbase or other vendor account.
"""

from __future__ import annotations

import threading
from typing import Any

from src.computer.browse import (
    Page,
    UnsafeURLError,
    assert_public_url,
    extract_readable,
    normalize_url,
)
from src.config import USER_AGENT

PLAYWRIGHT_TIMEOUT_MS = 20_000
_INSTALL_HINT = (
    "Playwright is not ready. Install it locally with "
    "`pip install playwright` and `playwright install chromium` "
    "(no extra API key)."
)

_lock = threading.Lock()
_playwright: Any = None
_browser: Any = None


class PlaywrightUnavailableError(UnsafeURLError):
    """Raised when Playwright or Chromium is not installed locally."""

    def __init__(self, message: str = _INSTALL_HINT):
        super().__init__(message, retryable=False)


def playwright_available() -> bool:
    """True when the Playwright Python package can be imported."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def fetch_page_playwright(
    url: str,
    *,
    resolver=None,
    timeout_ms: int = PLAYWRIGHT_TIMEOUT_MS,
) -> Page:
    """Render a public page in headless Chromium and extract readable text."""
    current = normalize_url(url)
    assert_public_url(current, resolver=resolver)
    browser = _ensure_browser()
    context = None
    page = None
    try:
        from playwright.sync_api import Error as PlaywrightError
    except ImportError as exc:
        raise PlaywrightUnavailableError() from exc
    try:
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.goto(current, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=min(8_000, timeout_ms))
        except PlaywrightError:
            pass
        final = str(page.url or current)
        assert_public_url(final, resolver=resolver)
        html = page.content() or ""
        title = str(page.title() or "")
        readable_title, text, links = extract_readable(html, base_url=final)
        return Page(
            url=final,
            title=readable_title or title,
            text=text,
            links=links,
            status=200,
        )
    except PlaywrightUnavailableError:
        raise
    except UnsafeURLError:
        raise
    except Exception as exc:
        message = str(exc)
        if _missing_browser_binary(message):
            raise PlaywrightUnavailableError(
                "Playwright Chromium is not installed. Run: playwright install chromium"
            ) from exc
        raise UnsafeURLError(f"Could not render that page: {exc}") from exc
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


def close_playwright() -> None:
    """Shut down a cached Chromium process (tests and process exit)."""
    global _playwright, _browser
    with _lock:
        browser = _browser
        playwright = _playwright
        _browser = None
        _playwright = None
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
    if playwright is not None:
        try:
            playwright.stop()
        except Exception:
            pass


def _ensure_browser() -> Any:
    global _playwright, _browser
    with _lock:
        if _browser is not None:
            return _browser
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PlaywrightUnavailableError() from exc
        try:
            _playwright = sync_playwright().start()
            _browser = _playwright.chromium.launch(headless=True)
        except PlaywrightUnavailableError:
            raise
        except Exception as exc:
            _playwright = None
            _browser = None
            message = str(exc)
            if _missing_browser_binary(message):
                raise PlaywrightUnavailableError(
                    "Playwright Chromium is not installed. Run: playwright install chromium"
                ) from exc
            raise UnsafeURLError(f"Could not start a local browser: {exc}") from exc
        return _browser


def _missing_browser_binary(message: str) -> bool:
    lowered = (message or "").lower()
    return (
        "executable doesn't exist" in lowered
        or "playwright install" in lowered
        or "browserType.launch" in message
    )
