"""Public-web research via Wikipedia and key-free web sources."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

from src.computer.browse import UnsafeURLError, fetch_page
from src.config import USER_AGENT
from src.tools.base_tool import Tool

_WIKI_SEARCH = "https://en.wikipedia.org/w/api.php"
_WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
_DDG = "https://api.duckduckgo.com/"
_DDG_HTML = "https://html.duckduckgo.com/html/"
_STACKEXCHANGE = "https://api.stackexchange.com/2.3/search/excerpts"

_WIKI_CANDIDATES = 5
_WIKI_SUMMARIES = 3
_WEB_RESULTS = 5
_PAGE_EXTRACT_CHARS = 900
_STACK_RESULTS = 3

# Conservative hostnames so "U.S. Navy" or "notes.md" are not treated as sites.
_COMMON_TLDS = frozenset(
    {
        "ai",
        "app",
        "au",
        "blog",
        "ca",
        "cloud",
        "co",
        "com",
        "dev",
        "edu",
        "fr",
        "gg",
        "gov",
        "in",
        "info",
        "io",
        "jp",
        "me",
        "net",
        "online",
        "org",
        "sh",
        "site",
        "tech",
        "to",
        "tv",
        "uk",
        "us",
        "wiki",
        "xyz",
    }
)
_FILE_TLDS = frozenset(
    {
        "css",
        "csv",
        "exe",
        "gif",
        "html",
        "jpeg",
        "jpg",
        "js",
        "json",
        "md",
        "pdf",
        "png",
        "py",
        "svg",
        "ts",
        "txt",
        "zip",
    }
)
_URLISH = re.compile(
    r"(?i)(?<!@)\b((?:https?://)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+(?:/[^\s]*)?)"
)

OpenPage = Callable[[str], object]


class ResearchTool(Tool):
    def __init__(
        self,
        session: requests.Session | None = None,
        open_page: OpenPage | None = None,
    ):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.open_page = open_page

    def name(self) -> str:
        return "research"

    def description(self) -> str:
        return (
            "Look up facts and background on a topic using Wikipedia, "
            "a named public website, DuckDuckGo, and Stack Overflow. "
            "Pass a search query. No extra API key needed."
        )

    def use(self, args) -> str:
        query = _normalize_query(args)
        if not query:
            return "Please provide a research query as a string"

        wiki = self._wikipedia(query)
        if wiki:
            return wiki

        mentioned = self._mentioned_page(query)
        if mentioned:
            return mentioned

        ddg = self._duckduckgo(query)
        if ddg:
            return ddg

        web = self._web_search(query)
        if web:
            return web

        stack = self._stackexchange(query)
        if stack:
            return stack

        return (
            f"I could not find a reliable public source for '{query}'. "
            "Try a more specific person, place, or topic."
        )

    def _wikipedia(self, query: str) -> str | None:
        try:
            search = self.session.get(
                _WIKI_SEARCH,
                params={
                    "action": "opensearch",
                    "search": query,
                    "limit": _WIKI_CANDIDATES,
                    "namespace": 0,
                    "format": "json",
                },
                timeout=10,
            )
            search.raise_for_status()
            payload = search.json()
        except (requests.exceptions.RequestException, ValueError):
            return None

        titles = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
        if not isinstance(titles, list):
            return None

        tried = 0
        for raw_title in titles:
            title = str(raw_title or "").strip()
            if not title:
                continue
            tried += 1
            if tried > _WIKI_SUMMARIES:
                break
            summary = self._wikipedia_summary(title)
            if summary:
                return summary
        return None

    def _wikipedia_summary(self, title: str) -> str | None:
        try:
            summary = self.session.get(
                _WIKI_SUMMARY.format(title=quote(title.replace(" ", "_"), safe="_")),
                timeout=10,
            )
            summary.raise_for_status()
            data = summary.json()
        except (requests.exceptions.RequestException, ValueError):
            return None

        if not isinstance(data, dict):
            return None
        if str(data.get("type") or "").strip().lower() == "disambiguation":
            return None
        extract = (data.get("extract") or "").strip()
        if not extract:
            return None
        url = data.get("content_urls", {}).get("desktop", {}).get("page") or data.get(
            "content_url"
        )
        heading = data.get("title") or title
        lines = [f"{heading}: {extract}"]
        if url:
            lines.append(f"Source: {url}")
        return "\n".join(lines)

    def _duckduckgo(self, query: str) -> str | None:
        try:
            response = self.session.get(
                _DDG,
                params={
                    "q": query,
                    "format": "json",
                    "no_html": 1,
                    "skip_disambig": 1,
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.exceptions.RequestException, ValueError):
            return None

        abstract = (data.get("AbstractText") or data.get("Abstract") or "").strip()
        heading = (data.get("Heading") or query).strip()
        url = (data.get("AbstractURL") or "").strip()
        related: list[str] = []
        for topic in data.get("RelatedTopics") or []:
            if isinstance(topic, dict) and topic.get("Text"):
                related.append(f"- {topic['Text']}")
            if len(related) >= 3:
                break

        if not abstract and not related:
            return None

        lines = [f"{heading}: {abstract}" if abstract else heading]
        if related:
            lines.append("Related:")
            lines.extend(related)
        if url:
            lines.append(f"Source: {url}")
        return "\n".join(lines)

    def _web_search(self, query: str) -> str | None:
        """Public HTML search when Wikipedia and Instant Answers miss."""
        try:
            response = self.session.get(
                _DDG_HTML,
                params={"q": query},
                timeout=12,
            )
            response.raise_for_status()
            html = response.text or ""
        except (requests.exceptions.RequestException, ValueError):
            return None

        hits = parse_ddg_html(html)[:_WEB_RESULTS]
        if not hits:
            return None
        # Captcha / bot-check pages have no result__a links.

        lines = [f"Web results for '{query}':"]
        extract = self._top_page_extract(hits[0].url)
        if extract:
            lines.append(extract)
            lines.append("Other sources:")
        for hit in hits:
            snippet = f" — {hit.snippet}" if hit.snippet else ""
            lines.append(f"- {hit.title}{snippet}")
            if hit.url:
                lines.append(f"  {hit.url}")
        return "\n".join(lines)

    def _top_page_extract(self, url: str) -> str | None:
        opener = self.open_page
        if opener is None:
            opener = self._fetch_public_page
        try:
            page = opener(url)
        except Exception:
            return None
        if page is None:
            return None
        title = str(getattr(page, "title", "") or "").strip()
        text = str(getattr(page, "text", "") or "").strip()
        final_url = str(getattr(page, "url", "") or url).strip()
        if not text:
            return None
        if len(text) > _PAGE_EXTRACT_CHARS:
            text = text[: _PAGE_EXTRACT_CHARS - 1].rstrip() + "…"
        heading = title or final_url or "Top result"
        return f"{heading}: {text}\nSource: {final_url}"

    def _mentioned_page(self, query: str) -> str | None:
        """If the user named a public site, read it — same SSRF rules as computer use."""
        urls = extract_mentioned_urls(query)
        if not urls:
            return None
        extract = self._top_page_extract(urls[0])
        if not extract:
            return None
        return f"From the site mentioned in '{query}':\n{extract}"

    def _stackexchange(self, query: str) -> str | None:
        try:
            response = self.session.get(
                _STACKEXCHANGE,
                params={
                    "order": "desc",
                    "sort": "relevance",
                    "q": query,
                    "site": "stackoverflow",
                    "pagesize": _STACK_RESULTS,
                    "filter": "default",
                },
                timeout=12,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.exceptions.RequestException, ValueError):
            return None

        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            return None

        lines = [f"Stack Overflow results for '{query}':"]
        seen: set[int] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            question_id = item.get("question_id")
            try:
                qid = int(question_id)
            except (TypeError, ValueError):
                continue
            if qid in seen:
                continue
            seen.add(qid)
            title = _strip_html(item.get("title") or "") or f"Question {qid}"
            excerpt = _strip_html(item.get("excerpt") or item.get("body") or "")
            url = f"https://stackoverflow.com/questions/{qid}"
            lines.append(f"- {title}")
            if excerpt:
                if len(excerpt) > 320:
                    excerpt = excerpt[:319].rstrip() + "…"
                lines.append(f"  {excerpt}")
            lines.append(f"  {url}")
            if len(seen) >= _STACK_RESULTS:
                break
        if len(lines) == 1:
            return None
        return "\n".join(lines)

    def _fetch_public_page(self, url: str):
        try:
            return fetch_page(url, session=self.session)
        except UnsafeURLError:
            return None


@dataclass(frozen=True)
class WebHit:
    title: str
    url: str
    snippet: str = ""


def unwrap_ddg_url(href: str) -> str:
    """Turn a DuckDuckGo redirect into the public destination URL."""
    text = (href or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if host.endswith("duckduckgo.com") and "/l/" in (parsed.path or ""):
        target = (parse_qs(parsed.query).get("uddg") or [""])[0]
        if target:
            return unquote(target)
    return text


def parse_ddg_html(html: str) -> list[WebHit]:
    """Collect title/snippet/url triples from DuckDuckGo HTML search."""
    parser = _DdgHtmlParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass
    hits: list[WebHit] = []
    seen: set[str] = set()
    for hit in parser.hits:
        url = unwrap_ddg_url(hit.url)
        title = _clean_space(hit.title) or url
        if not url or url in seen:
            continue
        if not _looks_like_result_url(url):
            continue
        seen.add(url)
        hits.append(WebHit(title=title, url=url, snippet=_clean_space(hit.snippet)))
    return hits


def _looks_like_result_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host.endswith("duckduckgo.com") or host in {"duck.com", "www.duck.com"}:
        return False
    return True


def extract_mentioned_urls(query: str) -> list[str]:
    """Pick public http(s) sites the user named in the research query."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _URLISH.finditer(query or ""):
        raw = match.group(1).rstrip(".,);]>\"'")
        if "://" not in raw:
            raw = "https://" + raw
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        if not host or parsed.scheme not in {"http", "https"}:
            continue
        labels = [part for part in host.split(".") if part]
        if len(labels) < 2:
            continue
        tld = labels[-1]
        if tld in _FILE_TLDS or tld not in _COMMON_TLDS:
            continue
        core = labels[1:] if labels[0] == "www" else labels
        if any(len(part) < 2 for part in core):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        found.append(raw)
    return found


def _strip_html(text: str) -> str:
    return _clean_space(html.unescape(re.sub(r"<[^>]+>", " ", text or "")))


def _normalize_query(args) -> str | None:
    if args is None:
        return None
    if isinstance(args, dict):
        value = (
            args.get("query")
            or args.get("q")
            or args.get("topic")
            or args.get("input")
            or args.get("args")
        )
        return str(value).strip() if value else None
    text = str(args).strip()
    return text or None


def _clean_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _attr(attrs: list[tuple[str, str | None]], name: str) -> str | None:
    wanted = name.lower()
    for key, value in attrs:
        if key.lower() == wanted and value:
            return value
    return None


class _DdgHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hits: list[WebHit] = []
        self._href: str | None = None
        self._kind: str | None = None
        self._parts: list[str] = []
        self._by_url: dict[str, dict[str, str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        classes = (_attr(attrs, "class") or "").split()
        href = _attr(attrs, "href")
        if not href:
            return
        if "result__a" in classes:
            self._begin(href, "title")
        elif "result__snippet" in classes:
            self._begin(href, "snippet")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href or not self._kind:
            return
        text = "".join(self._parts)
        entry = self._by_url.setdefault(self._href, {"title": "", "snippet": ""})
        entry[self._kind] = text
        self._href = None
        self._kind = None
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def close(self) -> None:
        super().close()
        for href, parts in self._by_url.items():
            self.hits.append(
                WebHit(
                    title=parts.get("title") or "",
                    url=href,
                    snippet=parts.get("snippet") or "",
                )
            )

    def _begin(self, href: str, kind: str) -> None:
        self._href = href
        self._kind = kind
        self._parts = []
