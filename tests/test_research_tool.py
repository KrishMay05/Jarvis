from unittest.mock import Mock

import requests

from src.computer.browse import Page
from src.tools.research_tool import (
    ResearchTool,
    extract_mentioned_urls,
    parse_ddg_html,
    unwrap_ddg_url,
)


def _session_with_headers() -> Mock:
    session = Mock()
    session.headers = {}
    return session


def _json_response(payload):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    response.text = ""
    return response


def _html_response(html: str):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("not json")
    response.text = html
    return response


def _empty_wiki():
    return _json_response(["q", [], [], []])


def _empty_ddg():
    return _json_response(
        {
            "Heading": "",
            "AbstractText": "",
            "AbstractURL": "",
            "RelatedTopics": [],
        }
    )


DDG_HTML = """
<html><body>
  <div class="result">
    <h2 class="result__title">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpython">
        Python (programming language)
      </a>
    </h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpython">
      Python is a high-level, general-purpose programming language.
    </a>
  </div>
  <div class="result">
    <a class="result__a" href="https://docs.python.org/3/">Python 3 docs</a>
    <a class="result__snippet" href="https://docs.python.org/3/">Official tutorial and library reference.</a>
  </div>
  <a class="result__a" href="https://duckduckgo.com/about">About DuckDuckGo</a>
</body></html>
"""


def test_research_tool_uses_wikipedia_summary():
    session = _session_with_headers()
    search = _json_response(
        [
            "James Webb",
            ["James Webb Space Telescope"],
            ["NASA infrared observatory"],
            ["https://en.wikipedia.org/wiki/James_Webb_Space_Telescope"],
        ]
    )
    summary = _json_response(
        {
            "title": "James Webb Space Telescope",
            "type": "standard",
            "extract": "A space telescope designed chiefly to conduct infrared astronomy.",
            "content_urls": {
                "desktop": {
                    "page": "https://en.wikipedia.org/wiki/James_Webb_Space_Telescope"
                }
            },
        }
    )
    session.get.side_effect = [search, summary]

    result = ResearchTool(session=session).use("James Webb Space Telescope")

    assert "infrared astronomy" in result
    assert "James Webb Space Telescope" in result
    assert "wikipedia.org" in result
    assert session.get.call_count == 2


def test_research_tool_skips_disambiguation_for_next_wiki_title():
    session = _session_with_headers()
    search = _json_response(
        [
            "Mercury",
            ["Mercury", "Mercury (planet)"],
            ["disambiguation", "planet"],
            [
                "https://en.wikipedia.org/wiki/Mercury",
                "https://en.wikipedia.org/wiki/Mercury_(planet)",
            ],
        ]
    )
    disambiguation = _json_response(
        {
            "title": "Mercury",
            "type": "disambiguation",
            "extract": "Mercury may refer to a planet, a metal, or a Roman god.",
        }
    )
    planet = _json_response(
        {
            "title": "Mercury (planet)",
            "type": "standard",
            "extract": "The smallest planet in the Solar System.",
            "content_urls": {
                "desktop": {"page": "https://en.wikipedia.org/wiki/Mercury_(planet)"}
            },
        }
    )
    session.get.side_effect = [search, disambiguation, planet]

    result = ResearchTool(session=session).use("Mercury planet")

    assert "smallest planet" in result
    assert "Mercury (planet)" in result
    assert session.get.call_count == 3


def test_research_tool_falls_back_to_duckduckgo():
    session = _session_with_headers()
    ddg = _json_response(
        {
            "Heading": "Python",
            "AbstractText": "A programming language.",
            "AbstractURL": "https://www.python.org/",
            "RelatedTopics": [],
        }
    )
    session.get.side_effect = [_empty_wiki(), ddg]

    result = ResearchTool(session=session).use({"query": "Python"})
    assert "programming language" in result
    assert "python.org" in result


def test_research_tool_falls_back_to_web_search_and_page_extract():
    session = _session_with_headers()
    session.get.side_effect = [_empty_wiki(), _empty_ddg(), _html_response(DDG_HTML)]
    page = Page(
        url="https://example.com/python",
        title="Python",
        text="Python is widely used for automation and web services.",
    )

    result = ResearchTool(session=session, open_page=lambda url: page).use(
        "python programming language"
    )

    assert "Web results for 'python programming language'" in result
    assert "widely used for automation" in result
    assert "https://example.com/python" in result
    assert "Python 3 docs" in result
    assert "docs.python.org" in result
    assert "About DuckDuckGo" not in result
    assert session.get.call_count == 3


def test_research_tool_web_search_still_works_when_page_fetch_fails():
    session = _session_with_headers()
    session.get.side_effect = [_empty_wiki(), _empty_ddg(), _html_response(DDG_HTML)]

    def boom(_url):
        raise RuntimeError("blocked")

    result = ResearchTool(session=session, open_page=boom).use("python")

    assert "Web results for 'python'" in result
    assert "high-level, general-purpose" in result
    assert "https://example.com/python" in result


def test_research_tool_sets_user_agent():
    session = _session_with_headers()
    ResearchTool(session=session)
    assert "JarvisPersonalAssistant" in session.headers["User-Agent"]


def test_research_tool_requires_query():
    session = _session_with_headers()
    result = ResearchTool(session=session).use("")
    assert "query" in result.lower()
    session.get.assert_not_called()


def test_research_tool_reads_mentioned_public_site():
    session = _session_with_headers()
    session.get.side_effect = [_empty_wiki()]
    page = Page(
        url="https://wttr.in/:help",
        title="wttr.in",
        text="Usage: curl wttr.in for a weather report. No API key required.",
    )

    def open_page(url: str):
        assert url == "https://wttr.in"
        return page

    result = ResearchTool(session=session, open_page=open_page).use(
        "wttr.in weather API"
    )
    assert "site mentioned" in result.lower()
    assert "No API key required" in result
    assert "https://wttr.in/:help" in result
    assert session.get.call_count == 1


def test_research_tool_falls_back_to_stackexchange():
    session = _session_with_headers()
    se = _json_response(
        {
            "items": [
                {
                    "question_id": 42334085,
                    "title": "Parse <code>JSON</code> in a tmux status line",
                    "excerpt": "I want to parse JSON from a curl response in tmux.",
                }
            ]
        }
    )
    session.get.side_effect = [
        _empty_wiki(),
        _empty_ddg(),
        _html_response("<html><body>no results</body></html>"),
        se,
    ]

    result = ResearchTool(session=session).use("parse json in tmux")
    assert "Stack Overflow" in result
    assert "Parse JSON in a tmux status line" in result
    assert "stackoverflow.com/questions/42334085" in result
    assert session.get.call_count == 4


def test_research_tool_handles_total_miss():
    session = _session_with_headers()
    session.get.side_effect = [
        _empty_wiki(),
        _empty_ddg(),
        _html_response("<html><body>no results</body></html>"),
        _json_response({"items": []}),
    ]
    result = ResearchTool(session=session).use("zzzz-not-a-topic")
    assert "could not find" in result.lower()


def test_research_tool_handles_http_errors():
    session = _session_with_headers()
    session.get.side_effect = requests.exceptions.Timeout("timed out")
    result = ResearchTool(session=session).use("anything")
    assert "could not find" in result.lower()


def test_unwrap_ddg_url_decodes_uddg_redirect():
    href = (
        "//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FPython"
        "&rut=abc"
    )
    assert unwrap_ddg_url(href) == "https://en.wikipedia.org/wiki/Python"


def test_parse_ddg_html_skips_duckduckgo_chrome_links():
    hits = parse_ddg_html(DDG_HTML)
    urls = [hit.url for hit in hits]
    assert urls == ["https://example.com/python", "https://docs.python.org/3/"]
    assert hits[0].title == "Python (programming language)"
    assert "high-level" in hits[0].snippet


def test_extract_mentioned_urls_finds_hosts_and_ignores_prose():
    assert extract_mentioned_urls("wttr.in weather API") == ["https://wttr.in"]
    assert extract_mentioned_urls("see https://example.com/docs please") == [
        "https://example.com/docs"
    ]
    assert extract_mentioned_urls("The U.S. Navy and notes.md file") == []
    assert extract_mentioned_urls("email me at ada@example.com later") == []
