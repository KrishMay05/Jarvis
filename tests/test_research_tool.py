from unittest.mock import Mock

import requests

from src.tools.research_tool import ResearchTool


def _session_with_headers() -> Mock:
    session = Mock()
    session.headers = {}
    return session


def test_research_tool_uses_wikipedia_summary():
    session = _session_with_headers()
    search = Mock()
    search.raise_for_status.return_value = None
    search.json.return_value = [
        "James Webb",
        ["James Webb Space Telescope"],
        ["NASA infrared observatory"],
        ["https://en.wikipedia.org/wiki/James_Webb_Space_Telescope"],
    ]
    summary = Mock()
    summary.raise_for_status.return_value = None
    summary.json.return_value = {
        "title": "James Webb Space Telescope",
        "extract": "A space telescope designed chiefly to conduct infrared astronomy.",
        "content_urls": {
            "desktop": {
                "page": "https://en.wikipedia.org/wiki/James_Webb_Space_Telescope"
            }
        },
    }
    session.get.side_effect = [search, summary]

    result = ResearchTool(session=session).use("James Webb Space Telescope")

    assert "infrared astronomy" in result
    assert "James Webb Space Telescope" in result
    assert "wikipedia.org" in result
    assert session.get.call_count == 2


def test_research_tool_falls_back_to_duckduckgo():
    session = _session_with_headers()
    empty_wiki = Mock()
    empty_wiki.raise_for_status.return_value = None
    empty_wiki.json.return_value = ["q", [], [], []]
    ddg = Mock()
    ddg.raise_for_status.return_value = None
    ddg.json.return_value = {
        "Heading": "Python",
        "AbstractText": "A programming language.",
        "AbstractURL": "https://www.python.org/",
        "RelatedTopics": [],
    }
    session.get.side_effect = [empty_wiki, ddg]

    result = ResearchTool(session=session).use({"query": "Python"})
    assert "programming language" in result
    assert "python.org" in result


def test_research_tool_sets_user_agent():
    session = _session_with_headers()
    ResearchTool(session=session)
    assert "JarvisPersonalAssistant" in session.headers["User-Agent"]


def test_research_tool_requires_query():
    session = _session_with_headers()
    result = ResearchTool(session=session).use("")
    assert "query" in result.lower()
    session.get.assert_not_called()


def test_research_tool_handles_total_miss():
    session = _session_with_headers()
    empty = Mock()
    empty.raise_for_status.return_value = None
    empty.json.return_value = ["q", [], [], []]
    ddg = Mock()
    ddg.raise_for_status.return_value = None
    ddg.json.return_value = {
        "Heading": "",
        "AbstractText": "",
        "AbstractURL": "",
        "RelatedTopics": [],
    }
    session.get.side_effect = [empty, ddg]
    result = ResearchTool(session=session).use("zzzz-not-a-topic")
    assert "could not find" in result.lower()


def test_research_tool_handles_http_errors():
    session = _session_with_headers()
    session.get.side_effect = requests.exceptions.Timeout("timed out")
    result = ResearchTool(session=session).use("anything")
    assert "could not find" in result.lower()
