from unittest.mock import Mock

import pytest
import requests

from src.computer.browse import (
    UnsafeURLError,
    assert_public_url,
    extract_readable,
    fetch_page,
    normalize_url,
)
from src.computer.session import BrowserSession
from src.tools.computer_tool import ComputerTool

PUBLIC_IP = "93.184.216.34"
SAMPLE_HTML = """
<html>
  <head>
    <title>Example Domain</title>
    <script>alert('skip')</script>
    <style>body { color: red; }</style>
  </head>
  <body>
    <article>
      <p>This domain is for use in illustrative examples in documents.</p>
      <a href="/docs">Docs</a>
      <a href="https://www.iana.org/domains/example">IANA</a>
      <a href="javascript:void(0)">Nope</a>
    </article>
  </body>
</html>
"""


def public_resolver(host, port, *args, **kwargs):
    return [(0, 0, 0, "", (PUBLIC_IP, 0))]


def loopback_resolver(host, port, *args, **kwargs):
    return [(0, 0, 0, "", ("127.0.0.1", 0))]


def html_response(html, url="https://example.com/", status=200, content_type="text/html"):
    raw = html.encode("utf-8")
    resp = Mock()
    resp.status_code = status
    resp.is_redirect = status in {301, 302, 303, 307, 308}
    resp.headers = {"Content-Type": content_type}
    resp.url = url
    resp.encoding = "utf-8"
    resp.iter_content = lambda chunk_size=16384: [raw]
    resp.close = Mock()
    return resp


def test_extract_readable_strips_scripts_and_collects_links():
    title, text, links = extract_readable(SAMPLE_HTML, base_url="https://example.com/")
    assert title == "Example Domain"
    assert "illustrative examples" in text
    assert "alert" not in text
    assert "color: red" not in text
    hrefs = dict(links)
    assert hrefs["Docs"] == "https://example.com/docs"
    assert "IANA" in hrefs
    assert all(not href.startswith("javascript:") for _, href in links)


def test_normalize_url_adds_https():
    assert normalize_url("example.com/path") == "https://example.com/path"
    assert normalize_url("http://example.com") == "http://example.com"
    with pytest.raises(UnsafeURLError, match="http"):
        normalize_url("file:///etc/passwd")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/secret",
        "http://127.0.0.1/",
        "http://10.0.0.5/admin",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://printer.local/",
    ],
)
def test_assert_public_url_blocks_private_targets(url):
    with pytest.raises(UnsafeURLError):
        assert_public_url(url, resolver=public_resolver)


def test_assert_public_url_blocks_dns_to_loopback():
    with pytest.raises(UnsafeURLError, match="private or local"):
        assert_public_url("https://evil.example", resolver=loopback_resolver)


def test_fetch_page_reads_html(monkeypatch):
    session = Mock()
    session.headers = {}
    session.get.return_value = html_response(SAMPLE_HTML, url="https://example.com/")
    page = fetch_page(
        "https://example.com/",
        session=session,
        resolver=public_resolver,
    )
    assert page.title == "Example Domain"
    assert "illustrative examples" in page.text
    assert any(label == "Docs" for label, _ in page.links)
    session.get.assert_called_once()


def test_fetch_page_follows_redirects():
    session = Mock()
    session.headers = {}
    first = html_response("", url="https://example.com/old", status=302)
    first.headers["Location"] = "/new"
    second = html_response(SAMPLE_HTML, url="https://example.com/new")
    session.get.side_effect = [first, second]
    page = fetch_page("https://example.com/old", session=session, resolver=public_resolver)
    assert page.url == "https://example.com/new"
    assert page.title == "Example Domain"
    assert session.get.call_count == 2


def test_fetch_page_rejects_non_html():
    session = Mock()
    session.headers = {}
    session.get.return_value = html_response(
        "fake",
        url="https://example.com/pic.png",
        content_type="image/png",
    )
    with pytest.raises(UnsafeURLError, match="image/png"):
        fetch_page(
            "https://example.com/pic.png",
            session=session,
            resolver=public_resolver,
        )


def test_fetch_page_does_not_hit_network_for_localhost():
    session = Mock()
    session.headers = {}
    with pytest.raises(UnsafeURLError):
        fetch_page("http://127.0.0.1/", session=session, resolver=public_resolver)
    session.get.assert_not_called()


def test_fetch_page_blocks_redirect_to_localhost():
    session = Mock()
    session.headers = {}
    first = html_response("", url="https://example.com/", status=302)
    first.headers["Location"] = "http://127.0.0.1/secret"
    session.get.return_value = first
    with pytest.raises(UnsafeURLError):
        fetch_page("https://example.com/", session=session, resolver=public_resolver)
    assert session.get.call_count == 1


def test_fetch_page_http_error():
    session = Mock()
    session.headers = {}
    session.get.side_effect = requests.exceptions.Timeout("timed out")
    with pytest.raises(UnsafeURLError, match="Could not open"):
        fetch_page("https://example.com/", session=session, resolver=public_resolver)


def test_browser_session_open_and_follow():
    session = Mock()
    session.headers = {}
    first = html_response(SAMPLE_HTML, url="https://example.com/")
    second = html_response(
        "<html><head><title>Docs</title></head><body><p>API reference.</p></body></html>",
        url="https://example.com/docs",
    )
    session.get.side_effect = [first, second]
    browser = BrowserSession(session=session, resolver=public_resolver)
    opened = browser.open("https://example.com/")
    assert "Example Domain" in opened
    followed = browser.follow("Docs")
    assert "API reference" in followed
    assert "Docs" in browser.list_links() or "example.com/docs" in browser.current()


def test_browser_session_follow_without_page():
    browser = BrowserSession(session=Mock(headers={}), resolver=public_resolver)
    assert "Open a page first" in browser.follow("Docs")


def test_computer_tool_opens_url():
    session = Mock()
    session.headers = {}
    session.get.return_value = html_response(SAMPLE_HTML)
    tool = ComputerTool(BrowserSession(session=session, resolver=public_resolver))
    result = tool.use("https://example.com")
    assert "Example Domain" in result
    assert "illustrative examples" in result


def test_computer_tool_follow_and_links():
    session = Mock()
    session.headers = {}
    session.get.return_value = html_response(SAMPLE_HTML)
    tool = ComputerTool(BrowserSession(session=session, resolver=public_resolver))
    tool.use({"action": "open", "url": "https://example.com/"})
    listed = tool.use("links")
    assert "Docs" in listed
    assert "IANA" in listed
    session.get.return_value = html_response(
        "<html><head><title>IANA</title></head><body><p>Domain registry.</p></body></html>",
        url="https://www.iana.org/domains/example",
    )
    followed = tool.use({"action": "follow", "text": "IANA"})
    assert "Domain registry" in followed


def test_computer_tool_requires_url():
    tool = ComputerTool(BrowserSession(session=Mock(headers={}), resolver=public_resolver))
    result = tool.use("")
    assert "URL" in result


def test_computer_tool_sets_user_agent():
    session = Mock()
    session.headers = {}
    ComputerTool(BrowserSession(session=session, resolver=public_resolver))
    assert "JarvisPersonalAssistant" in session.headers["User-Agent"]


def test_main_browse_without_api_key(monkeypatch, capsys):
    import sys

    for var in (
        "JARVIS_LLM_PROVIDER",
        "JARVIS_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py", "--browse", "https://example.com"])
    monkeypatch.setattr(
        "src.tools.computer_tool.ComputerTool.use",
        lambda self, args: f"opened:{args}",
    )
    from main import main

    main()
    assert "opened:https://example.com" in capsys.readouterr().out
