"""Fetch public web pages and extract readable text — no extra API key.

This is the first computer-use layer: Jarvis can open a URL and read it
locally. Private/internal addresses are blocked so the assistant cannot be
tricked into scanning the LAN or cloud metadata.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests

from src.config import USER_AGENT

MAX_BYTES = 1_000_000
MAX_TEXT_CHARS = 6000
MAX_LINKS = 15
MAX_REDIRECTS = 6
FETCH_TIMEOUT = 15

_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "svg", "iframe", "template"}
)
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "article",
        "section",
        "blockquote",
        "pre",
        "ul",
        "ol",
        "header",
        "footer",
        "table",
        "dt",
        "dd",
    }
)
_LOCAL_HOSTS = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)
_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home")
_HTML_TYPES = ("text/html", "application/xhtml+xml", "application/xml", "text/xml", "text/plain")

Resolver = Callable[..., list]


class UnsafeURLError(ValueError):
    """Raised when a URL is not a public http(s) page Jarvis should open."""


@dataclass
class Page:
    url: str
    title: str
    text: str
    links: list[tuple[str, str]] = field(default_factory=list)
    status: int = 200

    def format(self) -> str:
        heading = self.title or self.url
        body = self.text or "(no readable text on this page)"
        lines = [f"{heading} ({self.url})", body]
        if self.links:
            lines.append("Links:")
            for label, href in self.links:
                lines.append(f"- {label}: {href}")
        return "\n".join(lines)


def normalize_url(raw: str) -> str:
    """Turn user input into an absolute http(s) URL."""
    text = (raw or "").strip()
    if not text:
        raise UnsafeURLError("Please provide a URL to open.")
    text = text.split()[0].strip("<>\"'")
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError("Only public http and https URLs can be opened.")
    if not parsed.netloc:
        raise UnsafeURLError("That URL is missing a host.")
    return text


def assert_public_url(url: str, resolver: Resolver | None = None) -> str:
    """Validate scheme/host and that DNS points at a public address."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError("Only public http and https URLs can be opened.")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed.")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise UnsafeURLError("That URL is missing a host.")
    if host in _LOCAL_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeURLError("Local and internal hostnames are not allowed.")
    _assert_public_host(host, resolver or socket.getaddrinfo)
    return url


def fetch_page(
    url: str,
    session: requests.Session | None = None,
    *,
    resolver: Resolver | None = None,
) -> Page:
    """GET a public page, following redirects, and extract readable text."""
    resolve = resolver or socket.getaddrinfo
    http = session or requests.Session()
    http.headers.setdefault("User-Agent", USER_AGENT)
    current = normalize_url(url)
    response: requests.Response | None = None
    try:
        for _ in range(MAX_REDIRECTS + 1):
            assert_public_url(current, resolver=resolve)
            response = http.get(
                current,
                timeout=FETCH_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
                location = (response.headers.get("Location") or "").strip()
                response.close()
                response = None
                if not location:
                    raise UnsafeURLError("Redirect was missing a Location header.")
                current = urljoin(current, location)
                continue
            return _page_from_response(response, current)
        raise UnsafeURLError("Too many redirects while opening that page.")
    except requests.exceptions.RequestException as exc:
        raise UnsafeURLError(f"Could not open that page: {exc}") from exc
    finally:
        if response is not None:
            response.close()


def _assert_public_host(host: str, resolver: Resolver) -> None:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not _is_public_ip(ip):
            raise UnsafeURLError("Private or local addresses are not allowed.")
        return
    try:
        infos = resolver(host, None)
    except OSError as exc:
        raise UnsafeURLError(f"Could not resolve {host}: {exc}") from exc
    if not infos:
        raise UnsafeURLError(f"Could not resolve {host}.")
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        candidate = ipaddress.ip_address(sockaddr[0])
        if not _is_public_ip(candidate):
            raise UnsafeURLError(
                f"{host} resolves to a private or local address, which is not allowed."
            )


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_global) and not ip.is_multicast


def _page_from_response(response: requests.Response, url: str) -> Page:
    status = response.status_code
    if status >= 400:
        raise UnsafeURLError(f"The page returned HTTP {status}.")
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if content_type and not any(content_type.startswith(kind) for kind in _HTML_TYPES):
        raise UnsafeURLError(
            f"That URL is {content_type or 'an unknown type'}, not a readable web page."
        )
    raw = _read_limited(response)
    charset = response.encoding or "utf-8"
    try:
        html = raw.decode(charset, errors="replace")
    except LookupError:
        html = raw.decode("utf-8", errors="replace")
    title, text, links = extract_readable(html, base_url=str(response.url or url))
    return Page(
        url=str(response.url or url),
        title=title,
        text=text,
        links=links,
        status=status,
    )


def _read_limited(response: requests.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=16_384):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_BYTES:
            chunks.append(chunk[: max(0, MAX_BYTES - (total - len(chunk)))])
            break
        chunks.append(chunk)
    return b"".join(chunks)


def extract_readable(html: str, base_url: str = "") -> tuple[str, str, list[tuple[str, str]]]:
    """Return (title, text, links) from HTML using the stdlib parser."""
    parser = _ReadableParser(base_url=base_url)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        # Malformed documents still often yield partial text.
        pass
    title = _clean_space(parser.title)
    text = _clean_space(parser.text())
    if len(text) > MAX_TEXT_CHARS:
        text = text[: MAX_TEXT_CHARS - 1].rstrip() + "…"
    links = _unique_links(parser.links)
    return title, text, links


def _unique_links(links: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for label, href in links:
        if href in seen:
            continue
        seen.add(href)
        unique.append((label or href, href))
        if len(unique) >= MAX_LINKS:
            break
    return unique


def _clean_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


class _ReadableParser(HTMLParser):
    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self._in_title = False
        self._skip = 0
        self._chunks: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._link_href: str | None = None
        self._link_text: list[str] = []

    @property
    def title(self) -> str:
        return "".join(self.title_parts)

    def text(self) -> str:
        return "".join(self._chunks)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name == "title" and self._skip == 0:
            self._in_title = True
            return
        if name in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if name in _BLOCK_TAGS:
            self._chunks.append(" ")
        if name == "a":
            href = _attr(attrs, "href")
            if href and not href.lower().startswith(("javascript:", "data:", "mailto:", "#")):
                self._link_href = urljoin(self.base_url, href)
                self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name == "title":
            self._in_title = False
            return
        if name in _SKIP_TAGS and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if name == "a" and self._link_href:
            label = _clean_space("".join(self._link_text)) or self._link_href
            self.links.append((label, self._link_href))
            self._link_href = None
            self._link_text = []
        if name in _BLOCK_TAGS:
            self._chunks.append(" ")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip:
            return
        self._chunks.append(data)
        if self._link_href is not None:
            self._link_text.append(data)


def _attr(attrs: list[tuple[str, str | None]], name: str) -> str | None:
    wanted = name.lower()
    for key, value in attrs:
        if key.lower() == wanted and value:
            return value
    return None
