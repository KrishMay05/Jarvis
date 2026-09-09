import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.config import LLMSettings, describe_runtime
from src.ui.server import MAX_BODY_BYTES, JarvisWebApp


class FakeOrchestrator:
    def __init__(self):
        self.memory = []
        self.memory_store = None
        self.automation_store = None
        self.calls: list[str] = []

    def handle_message(self, text: str) -> str:
        self.calls.append(text)
        return f"heard:{text}"

    def drain_due_automations(self):
        return ["Reminder: stretch"]


def test_index_is_self_contained_html():
    app = JarvisWebApp(missing_key="No AI API key found.")
    response = app.dispatch("GET", "/")
    html = response.body.decode("utf-8")
    assert response.status == 200
    assert "text/html" in response.content_type
    assert "Jarvis" in html
    assert "/api/chat" in html
    assert "/api/status" in html
    assert "http://" not in html.split("<style>")[1].split("</style>")[0]


def test_status_without_key_is_not_ready():
    app = JarvisWebApp(missing_key="No AI API key found.")
    response = app.dispatch("GET", "/api/status")
    payload = json.loads(response.body)
    assert response.status == 200
    assert payload["ready"] is False
    assert "No AI API key found" in payload["error"]
    assert "weather" in payload["tools"]
    assert "localhost" in payload["bind"]


def test_status_with_settings_lists_llm():
    settings = LLMSettings(provider="gemini", api_key="test", model="gemini-2.0-flash")
    app = JarvisWebApp(
        orchestrator=FakeOrchestrator(),
        settings=settings,
    )
    payload = json.loads(app.dispatch("GET", "/api/status").body)
    assert payload["ready"] is True
    assert payload["llm"]["provider"] == "gemini"
    assert "web UI" in payload["runtime"]


def test_chat_requires_key():
    app = JarvisWebApp(missing_key="No AI API key found.")
    response = app.dispatch(
        "POST", "/api/chat", json.dumps({"message": "hello"}).encode()
    )
    payload = json.loads(response.body)
    assert response.status == 503
    assert "No AI API key found" in payload["error"]


def test_chat_with_orchestrator_returns_reply_and_due_jobs():
    orch = FakeOrchestrator()
    app = JarvisWebApp(
        orchestrator=orch,
        settings=LLMSettings(provider="openai", api_key="sk-test", model="gpt-4o-mini"),
    )
    response = app.dispatch(
        "POST",
        "/api/chat",
        json.dumps({"message": "  What time is it?  "}).encode(),
    )
    payload = json.loads(response.body)
    assert response.status == 200
    assert payload["reply"] == "heard:What time is it?"
    assert payload["due"] == ["Reminder: stretch"]
    assert orch.calls == ["What time is it?"]
    assert orch.memory[-1] == "User: What time is it?"


def test_chat_rejects_empty_and_invalid_json():
    app = JarvisWebApp(orchestrator=FakeOrchestrator())
    empty = app.dispatch("POST", "/api/chat", b'{"message":"   "}')
    assert empty.status == 400
    bad = app.dispatch("POST", "/api/chat", b"not-json")
    assert bad.status == 400


def test_chat_rejects_oversized_body():
    app = JarvisWebApp(orchestrator=FakeOrchestrator())
    body = json.dumps({"message": "x" * (MAX_BODY_BYTES)}).encode()
    response = app.dispatch("POST", "/api/chat", body)
    assert response.status == 413


def test_unknown_route_and_health():
    app = JarvisWebApp(missing_key="missing")
    assert app.dispatch("GET", "/nope").status == 404
    health = json.loads(app.dispatch("GET", "/api/health").body)
    assert health == {"ok": True, "ready": False}


def test_http_server_roundtrip():
    settings = LLMSettings(provider="anthropic", api_key="sk-ant-x", model="claude-sonnet-4-5")
    app = JarvisWebApp(orchestrator=FakeOrchestrator(), settings=settings)
    httpd = app.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            html = resp.read().decode()
        assert "personal assistant" in html
        with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as resp:
            status = json.loads(resp.read())
        assert status["ready"] is True
        req = Request(
            f"http://127.0.0.1:{port}/api/chat",
            data=json.dumps({"message": "hello"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            chat = json.loads(resp.read())
        assert chat["reply"] == "heard:hello"
        try:
            urlopen(f"http://127.0.0.1:{port}/missing", timeout=5)
            raise AssertionError("expected 404")
        except HTTPError as exc:
            assert exc.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_describe_runtime_mentions_web_ui(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.delenv("JARVIS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("JARVIS_MODEL", raising=False)
    text = describe_runtime()
    assert "web UI" in text
    assert "--serve" in text
