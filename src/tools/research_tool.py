"""Public-web research via Wikipedia and DuckDuckGo — no extra API key."""

from __future__ import annotations

from urllib.parse import quote

import requests

from src.config import USER_AGENT
from src.tools.base_tool import Tool

_WIKI_SEARCH = "https://en.wikipedia.org/w/api.php"
_WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
_DDG = "https://api.duckduckgo.com/"


class ResearchTool(Tool):
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def name(self) -> str:
        return "research"

    def description(self) -> str:
        return (
            "Look up facts and background on a topic using Wikipedia and "
            "DuckDuckGo Instant Answers. Pass a search query. No extra API key needed."
        )

    def use(self, args) -> str:
        query = _normalize_query(args)
        if not query:
            return "Please provide a research query as a string"

        wiki = self._wikipedia(query)
        if wiki:
            return wiki

        ddg = self._duckduckgo(query)
        if ddg:
            return ddg

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
                    "limit": 1,
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
        if not titles:
            return None
        title = titles[0]

        try:
            summary = self.session.get(
                _WIKI_SUMMARY.format(title=quote(title.replace(" ", "_"), safe="_")),
                timeout=10,
            )
            summary.raise_for_status()
            data = summary.json()
        except (requests.exceptions.RequestException, ValueError):
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
