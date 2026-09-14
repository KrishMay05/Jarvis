import json
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.auth.store import AuthAccount, AuthStore
from src.automation.schedule import parse_schedule, utc_now
from src.automation.store import AutomationStore
from src.config import LLMSettings, describe_runtime
from src.ui.server import MAX_BODY_BYTES, JarvisWebApp

PASTE_KEY = "sk-ui-paste-secret-do-not-echo"


class FakeOrchestrator:
    def __init__(self):
        self.memory = []
        self.memory_store = None
        self.automation_store = None
        self.calls: list[str] = []

    def handle_message(self, text: str) -> str:
        self.calls.append(text)
        return f"heard:{text}"

    def drain_due_automations(self, now=None):
        if self.automation_store is not None:
            from src.automation.runner import run_due_jobs

            return run_due_jobs(
                self.automation_store, run_prompt=self.handle_message, now=now
            )
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
    assert "/api/due" in html
    assert "/api/auth/google/connect" in html
    assert "Fires in the background" in html
    assert "pollDue" in html
    assert "/api/key" in html
    assert "Connect Google" in html
    assert "Save key" in html
    assert "Paste Gemini" in html
    assert "http://" not in html.split("<style>")[1].split("</style>")[0]


def test_status_without_key_is_not_ready():
    app = JarvisWebApp(missing_key="No AI API key found.")
    response = app.dispatch("GET", "/api/status")
    payload = json.loads(response.body)
    assert response.status == 200
    assert payload["ready"] is False
    assert "No AI API key found" in payload["error"]
    assert "weather" in payload["tools"]
    assert payload["google"]["configured"] is False
    assert payload["google"]["connected"] is False
    assert "localhost" in payload["bind"]
    assert payload["can_install_key"] is True
    assert payload["scheduler"]["running"] is False
    assert payload["scheduler"]["pending_due"] == 0


def test_status_with_settings_lists_llm():
    settings = LLMSettings(provider="gemini", api_key="test", model="gemini-2.0-flash")
    app = JarvisWebApp(
        orchestrator=FakeOrchestrator(),
        settings=settings,
    )
    payload = json.loads(app.dispatch("GET", "/api/status").body)
    assert payload["ready"] is True
    assert payload["llm"]["provider"] == "gemini"
    assert payload["llm"]["fallbacks"] == []
    assert "web UI" in payload["runtime"]


def test_status_lists_optional_llm_fallbacks(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-primary")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-backup")
    settings = LLMSettings(provider="gemini", api_key="gemini-primary", model="gemini-2.0-flash")
    app = JarvisWebApp(orchestrator=FakeOrchestrator(), settings=settings)
    payload = json.loads(app.dispatch("GET", "/api/status").body)
    assert payload["llm"]["fallbacks"] == [
        {"provider": "openai", "model": "gpt-4o-mini", "summary": "openai (gpt-4o-mini)"}
    ]
    assert "Fallback LLM: openai (gpt-4o-mini)" in payload["runtime"]
    html = app.dispatch("GET", "/").body.decode("utf-8")
    assert "fallback ready" in html
    assert "optional backup" in html


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
    assert "paste an AI key" in text
    assert "Connect Google" in text


def test_connect_google_requires_client_id():
    app = JarvisWebApp(missing_key="No AI API key found.")
    response = app.dispatch("POST", "/api/auth/google/connect", b"")
    payload = json.loads(response.body)
    assert response.status == 400
    assert "GOOGLE_OAUTH_CLIENT_ID" in payload["error"]
    assert payload["configured"] is False


def test_connect_google_works_without_ai_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "abc.apps.googleusercontent.com")
    app = JarvisWebApp(
        missing_key="No AI API key found.",
        public_base="http://127.0.0.1:8787",
    )
    response = app.dispatch("POST", "/api/auth/google/connect", b"")
    payload = json.loads(response.body)
    assert response.status == 200
    assert payload["ok"] is True
    assert payload["auth_url"].startswith("https://accounts.google.com/")
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8787%2Foauth%2Fgoogle%2Fcallback" in payload[
        "auth_url"
    ]
    assert app._google_login is not None
    assert app.ready is False


def test_google_callback_without_pending_login():
    app = JarvisWebApp()
    response = app.dispatch("GET", "/oauth/google/callback?code=xyz&state=nope")
    assert response.status == 400
    assert "text/html" in response.content_type
    assert b"No Google login" in response.body


def test_google_callback_stores_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "abc.apps.googleusercontent.com")
    store = AuthStore(tmp_path / "auth.json")
    app = JarvisWebApp(auth_store=store, public_base="http://127.0.0.1:8787")
    started = app.dispatch("POST", "/api/auth/google/connect", b"")
    assert started.status == 200
    login = app._google_login

    def fake_finish(target_store, session, code, **kwargs):
        assert target_store is store
        assert session is login
        assert code == "oauth-code"
        account = AuthAccount(
            provider="google",
            access_token="ya29.ui",
            refresh_token="1//r",
            email="ada@example.com",
        )
        return target_store.put(account)

    monkeypatch.setattr("src.ui.server.finish_google_login", fake_finish)
    response = app.dispatch(
        "GET",
        f"/oauth/google/callback?code=oauth-code&state={login.state}",
    )
    assert response.status == 200
    assert b"ada@example.com" in response.body
    assert b"Continue to Jarvis" in response.body
    account = store.get("google")
    assert account is not None
    assert account.email == "ada@example.com"
    assert app._google_login is None
    status = json.loads(app.dispatch("GET", "/api/status").body)
    assert status["google"]["connected"] is True
    assert status["google"]["email"] == "ada@example.com"


def test_head_on_google_callback_does_not_finish_login(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "abc.apps.googleusercontent.com")
    app = JarvisWebApp(public_base="http://127.0.0.1:8787")
    app.dispatch("POST", "/api/auth/google/connect", b"")
    login = app._google_login
    called = {"n": 0}

    def boom(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("HEAD must not exchange the OAuth code")

    monkeypatch.setattr("src.ui.server.finish_google_login", boom)
    response = app.dispatch(
        "HEAD",
        f"/oauth/google/callback?code=oauth-code&state={login.state}",
    )
    assert response.status == 200
    assert called["n"] == 0
    assert app._google_login is login


def test_disconnect_google_from_ui(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    store.put(
        AuthAccount(
            provider="google",
            access_token="ya29.ui",
            email="ada@example.com",
        )
    )
    app = JarvisWebApp(auth_store=store)
    response = app.dispatch("POST", "/api/auth/google/disconnect", b"")
    payload = json.loads(response.body)
    assert response.status == 200
    assert payload["google"]["connected"] is False
    assert "Disconnected" in payload["message"]
    assert store.get("google") is None


def test_install_key_unlocks_chat_without_restart(tmp_path):
    env_path = tmp_path / "from-ui.env"
    built: list[LLMSettings] = []

    def factory(settings: LLMSettings):
        built.append(settings)
        return FakeOrchestrator()

    app = JarvisWebApp(
        missing_key="No AI API key found.",
        orchestrator_factory=factory,
        env_path=env_path,
    )
    assert app.ready is False
    response = app.dispatch(
        "POST",
        "/api/key",
        json.dumps({"api_key": PASTE_KEY, "provider": "auto"}).encode(),
    )
    payload = json.loads(response.body)
    assert response.status == 200
    assert payload["ok"] is True
    assert payload["ready"] is True
    assert payload["llm"]["provider"] == "openai"
    assert PASTE_KEY not in response.body.decode()
    assert PASTE_KEY not in json.dumps(payload)
    assert "no restart" in payload["message"].lower()
    assert app.ready is True
    assert built and built[0].api_key == PASTE_KEY
    assert env_path.exists()
    saved = env_path.read_text(encoding="utf-8")
    assert f"OPENAI_API_KEY={PASTE_KEY}" in saved
    assert "JARVIS_LLM_PROVIDER=openai" in saved

    status = json.loads(app.dispatch("GET", "/api/status").body)
    assert status["ready"] is True
    assert PASTE_KEY not in json.dumps(status)
    assert status["llm"]["summary"] == "openai (gpt-4o-mini)"

    chat = app.dispatch("POST", "/api/chat", json.dumps({"message": "hello"}).encode())
    assert chat.status == 200
    assert json.loads(chat.body)["reply"] == "heard:hello"


def test_install_key_rejects_empty_and_placeholder():
    app = JarvisWebApp(missing_key="No AI API key found.")
    empty = app.dispatch("POST", "/api/key", b'{"api_key":"   "}')
    assert empty.status == 400
    assert "Paste an AI API key" in json.loads(empty.body)["error"]
    placeholder = app.dispatch("POST", "/api/key", b'{"api_key":"your-gemini-key"}')
    assert placeholder.status == 400
    bad = app.dispatch("POST", "/api/key", b"not-json")
    assert bad.status == 400


def test_install_key_refuses_non_loopback_bind():
    app = JarvisWebApp(
        missing_key="No AI API key found.",
        public_base="http://192.168.1.20:8787",
    )
    response = app.dispatch(
        "POST",
        "/api/key",
        json.dumps({"api_key": PASTE_KEY}).encode(),
    )
    payload = json.loads(response.body)
    assert response.status == 403
    assert "localhost" in payload["error"]
    assert app.ready is False


def test_install_key_replaces_existing_orchestrator(tmp_path):
    first = FakeOrchestrator()
    first.closed = False

    def close() -> None:
        first.closed = True

    first.close = close
    built: list[FakeOrchestrator] = []

    def factory(settings: LLMSettings):
        nxt = FakeOrchestrator()
        nxt.settings = settings
        built.append(nxt)
        return nxt

    app = JarvisWebApp(
        orchestrator=first,
        settings=LLMSettings(provider="gemini", api_key="old-key-value", model="gemini-2.0-flash"),
        orchestrator_factory=factory,
        env_path=tmp_path / "swap.env",
    )
    response = app.dispatch(
        "POST",
        "/api/key",
        json.dumps({"api_key": "sk-ant-replacement-key", "provider": "anthropic"}).encode(),
    )
    assert response.status == 200
    assert first.closed is True
    assert app.orchestrator is built[0]
    assert built[0].settings.provider == "anthropic"
    assert json.loads(app.dispatch("GET", "/api/status").body)["llm"]["provider"] == "anthropic"


def test_http_install_key_roundtrip(tmp_path):
    env_path = tmp_path / "http.env"

    def factory(settings: LLMSettings):
        return FakeOrchestrator()

    app = JarvisWebApp(
        missing_key="No AI API key found.",
        orchestrator_factory=factory,
        env_path=env_path,
    )
    httpd = app.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        app.public_base = f"http://127.0.0.1:{port}"
        req = Request(
            f"http://127.0.0.1:{port}/api/key",
            data=json.dumps({"api_key": PASTE_KEY}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read())
        assert payload["ready"] is True
        assert PASTE_KEY not in json.dumps(payload)
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as resp:
            health = json.loads(resp.read())
        assert health == {"ok": True, "ready": True}
        chat_req = Request(
            f"http://127.0.0.1:{port}/api/chat",
            data=json.dumps({"message": "ping"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(chat_req, timeout=5) as resp:
            chat = json.loads(resp.read())
        assert chat["reply"] == "heard:ping"
        assert env_path.exists()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_google_connect_roundtrip(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "abc.apps.googleusercontent.com")
    app = JarvisWebApp(missing_key="No AI API key found.")
    httpd = app.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        app.public_base = f"http://127.0.0.1:{port}"
        req = Request(
            f"http://127.0.0.1:{port}/api/auth/google/connect",
            data=b"",
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read())
        assert payload["ok"] is True
        assert f"127.0.0.1%3A{port}" in payload["auth_url"]
        with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as resp:
            status = json.loads(resp.read())
        assert status["ready"] is False
        assert status["google"]["configured"] is True
        assert status["google"]["connected"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()


FIXED = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)


def test_ticker_fires_reminder_without_ai_key():
    store = AutomationStore()
    store.add(
        kind="remind",
        title="stretch",
        schedule=parse_schedule("in 5 minutes", now=FIXED),
        message="stretch",
    )
    app = JarvisWebApp(missing_key="No AI API key found.")
    due = app.tick_due_automations(now=FIXED + timedelta(minutes=6))
    assert len(due) == 1
    assert "stretch" in due[0]
    payload = json.loads(app.dispatch("GET", "/api/due").body)
    assert payload["due"] == due
    empty = json.loads(app.dispatch("GET", "/api/due").body)
    assert empty["due"] == []
    listed = json.loads(app.dispatch("GET", "/api/automations").body)
    assert "stretch" in listed["text"]


def test_ticker_run_job_needs_orchestrator_then_runs():
    store = AutomationStore()
    store.add(
        kind="run",
        title="news",
        schedule=parse_schedule("in 1 minutes", now=FIXED),
        prompt="Research the latest AI news",
    )
    paused = JarvisWebApp(missing_key="No AI API key found.")
    skipped = paused.tick_due_automations(now=FIXED + timedelta(minutes=2))
    assert skipped == []
    assert AutomationStore().due_jobs(FIXED + timedelta(minutes=2))

    orch = FakeOrchestrator()
    orch.automation_store = AutomationStore()
    app = JarvisWebApp(
        orchestrator=orch,
        settings=LLMSettings(provider="openai", api_key="sk-test", model="gpt-4o-mini"),
    )
    due = app.tick_due_automations(now=FIXED + timedelta(minutes=2))
    assert len(due) == 1
    assert "heard:Research the latest AI news" in due[0]
    assert orch.calls == ["Research the latest AI news"]
    assert json.loads(app.dispatch("GET", "/api/due").body)["due"] == due


def test_chat_consumes_queued_due_jobs():
    store = AutomationStore()
    store.add(
        kind="remind",
        title="water",
        schedule=parse_schedule("in 1 minutes", now=FIXED),
        message="drink water",
    )
    orch = FakeOrchestrator()
    orch.automation_store = store
    app = JarvisWebApp(
        orchestrator=orch,
        settings=LLMSettings(provider="openai", api_key="sk-test", model="gpt-4o-mini"),
    )
    app.tick_due_automations(now=FIXED + timedelta(minutes=2))
    response = app.dispatch(
        "POST",
        "/api/chat",
        json.dumps({"message": "hello"}).encode(),
    )
    payload = json.loads(response.body)
    assert response.status == 200
    assert payload["reply"] == "heard:hello"
    assert any("drink water" in line for line in payload["due"])
    assert json.loads(app.dispatch("GET", "/api/due").body)["due"] == []


def test_start_ticker_fires_overdue_reminder():
    store = AutomationStore()
    store.add(
        kind="remind",
        title="stand",
        schedule=parse_schedule("in 1 seconds", now=utc_now() - timedelta(seconds=5)),
        message="stand up",
    )
    app = JarvisWebApp(missing_key="No AI API key found.")
    app.tick_seconds = 0.05
    app.start_ticker()
    try:
        deadline = time.time() + 2
        due = []
        while time.time() < deadline:
            due = app.take_due()
            if due:
                break
            time.sleep(0.05)
        assert due
        assert any("stand up" in line for line in due)
        status = json.loads(app.dispatch("GET", "/api/status").body)
        assert status["scheduler"]["running"] is True
    finally:
        app.stop_ticker()
    assert json.loads(app.dispatch("GET", "/api/status").body)["scheduler"]["running"] is False


def test_http_due_poll_roundtrip():
    store = AutomationStore()
    store.add(
        kind="remind",
        title="tea",
        schedule=parse_schedule("in 1 minutes", now=FIXED),
        message="tea is ready",
    )
    app = JarvisWebApp(missing_key="No AI API key found.")
    app.tick_due_automations(now=FIXED + timedelta(minutes=2))
    httpd = app.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/api/due", timeout=5) as resp:
            payload = json.loads(resp.read())
        assert any("tea is ready" in line for line in payload["due"])
        with urlopen(f"http://127.0.0.1:{port}/api/due", timeout=5) as resp:
            empty = json.loads(resp.read())
        assert empty["due"] == []
    finally:
        httpd.shutdown()
        httpd.server_close()
