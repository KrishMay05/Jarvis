"""Localhost web UI for Jarvis — no extra API key.

Bind to loopback by default so the assistant is not exposed on the LAN.
Chat still uses the same Gemini/OpenAI/Anthropic key as the REPL.
"""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from src.auth.oauth import (
    OAuthError,
    begin_google_login,
    disconnect_google,
    finish_google_login,
    google_client_id,
    missing_client_id_message,
    parse_oauth_callback,
)
from src.auth.store import AuthStore, auth_status_line
from src.automation.runner import run_due_jobs
from src.automation.store import AutomationStore, automation_status_line
from src.config import (
    InvalidAPIKeyError,
    LLMSettings,
    MissingAPIKeyError,
    describe_runtime,
    install_llm_key,
    list_llm_settings,
)
from src.llm import reset_failover_state
from src.mcp.config import mcp_status_line
from src.memory.store import MemoryStore, memory_status_line

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_TICK_SECONDS = 15.0
MAX_BODY_BYTES = 32_768
_MAX_DUE_INBOX = 50
_INDEX_PATH = Path(__file__).with_name("index.html")

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})


@dataclass(frozen=True)
class UiResponse:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"


class JarvisWebApp:
    """Dispatch HTTP requests for the local chat UI."""

    def __init__(
        self,
        orchestrator=None,
        settings: LLMSettings | None = None,
        missing_key: str | None = None,
        auth_store: AuthStore | None = None,
        public_base: str | None = None,
        orchestrator_factory: Callable | None = None,
        env_path: Path | str | None = None,
    ):
        self.orchestrator = orchestrator
        self.settings = settings
        self.missing_key = missing_key
        self.auth_store = _resolve_auth_store(orchestrator, auth_store)
        self.public_base = (public_base or f"http://{DEFAULT_HOST}:{DEFAULT_PORT}").rstrip(
            "/"
        )
        self.orchestrator_factory = orchestrator_factory
        self.env_path = Path(env_path).expanduser() if env_path else None
        self._google_login = None
        self._lock = threading.RLock()
        self._due_inbox: list[str] = []
        self._ticker_stop = threading.Event()
        self._ticker_thread: threading.Thread | None = None
        self.tick_seconds = _tick_seconds()

    def dispatch(self, method: str, path: str, body: bytes = b"") -> UiResponse:
        verb = (method or "GET").upper()
        route = urlparse(path or "/").path or "/"
        if route != "/" and route.endswith("/"):
            route = route.rstrip("/")

        if verb == "GET" and route in {"/", "/index.html"}:
            return UiResponse(
                200,
                _index_html(),
                "text/html; charset=utf-8",
            )
        if verb == "GET" and route == "/api/status":
            return _json(200, self.status_payload())
        if verb == "GET" and route == "/api/health":
            return _json(200, {"ok": True, "ready": self.ready})
        if verb == "GET" and route == "/api/automations":
            return _json(200, {"text": self._automation_text()})
        if verb == "GET" and route == "/api/due":
            return _json(200, {"due": self.take_due()})
        if verb == "POST" and route == "/api/chat":
            return self._chat(body)
        if verb == "POST" and route == "/api/key":
            return self._install_key(body)
        if verb == "POST" and route == "/api/auth/google/connect":
            return self._connect_google()
        if verb == "POST" and route == "/api/auth/google/disconnect":
            return self._disconnect_google()
        if verb == "GET" and route == "/oauth/google/callback":
            return self._google_callback(path)
        if verb == "HEAD" and route == "/oauth/google/callback":
            return UiResponse(200, b"", "text/html; charset=utf-8")
        if verb == "HEAD" and route in {
            "/",
            "/index.html",
            "/api/status",
            "/api/health",
            "/api/automations",
            "/api/due",
        }:
            return self.dispatch("GET", path, b"")
        if verb not in {"GET", "POST"}:
            return _json(405, {"error": "Method not allowed."})
        return _json(404, {"error": "Not found."})

    @property
    def ready(self) -> bool:
        return self.orchestrator is not None and not self.missing_key

    def status_payload(self) -> dict:
        orch = self.orchestrator
        memory = getattr(orch, "memory_store", None) if orch is not None else None
        automations = (
            getattr(orch, "automation_store", None) if orch is not None else None
        )
        llm = None
        runtime = None
        if self.settings is not None:
            llm = {
                "provider": self.settings.provider,
                "model": self.settings.model,
                "summary": self.settings.summary(),
                "fallbacks": _fallback_summaries(self.settings),
            }
            try:
                runtime = describe_runtime(self.settings)
            except MissingAPIKeyError:
                runtime = None
        return {
            "ok": True,
            "ready": self.ready,
            "error": self.missing_key,
            "llm": llm,
            "runtime": runtime,
            "tools": [
                "weather",
                "time",
                "research",
                "chat",
                "memory",
                "automations",
                "computer",
                "mail",
                "calendar",
                "mcp",
            ],
            "memory": memory_status_line(memory if isinstance(memory, MemoryStore) else None),
            "automations": automation_status_line(
                automations if isinstance(automations, AutomationStore) else None
            ),
            "auth": auth_status_line(self.auth_store),
            "google": self._google_status(),
            "mcp": mcp_status_line(),
            "bind": "localhost only — not exposed on your LAN",
            "can_install_key": self._accepts_key_install(),
            "scheduler": self._scheduler_status(),
        }

    def _google_status(self) -> dict:
        account = self.auth_store.get("google")
        connected = bool(account is not None and account.access_token)
        return {
            "configured": bool(google_client_id()),
            "connected": connected,
            "email": (account.email if account is not None else "") or "",
        }

    def _automation_text(self) -> str:
        return self._automation_store().format_list()

    def _automation_store(self) -> AutomationStore:
        orch = self.orchestrator
        store = getattr(orch, "automation_store", None) if orch is not None else None
        if isinstance(store, AutomationStore):
            return store
        return AutomationStore()

    def _scheduler_status(self) -> dict:
        thread = self._ticker_thread
        with self._lock:
            pending = len(self._due_inbox)
        return {
            "running": bool(thread is not None and thread.is_alive()),
            "interval_seconds": self.tick_seconds,
            "pending_due": pending,
        }

    def start_ticker(self) -> None:
        """Fire due automations in the background while the UI is open."""
        if self._ticker_thread is not None and self._ticker_thread.is_alive():
            return
        self._ticker_stop.clear()
        thread = threading.Thread(
            target=self._ticker_loop,
            name="jarvis-automation-ticker",
            daemon=True,
        )
        self._ticker_thread = thread
        thread.start()

    def stop_ticker(self) -> None:
        self._ticker_stop.set()
        thread = self._ticker_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._ticker_thread = None

    def _ticker_loop(self) -> None:
        while True:
            try:
                self.tick_due_automations()
            except Exception:
                pass
            if self._ticker_stop.wait(self.tick_seconds):
                break

    def tick_due_automations(self, now=None) -> list[str]:
        """Run due jobs and queue their reports for the UI. Safe for tests."""
        with self._lock:
            due = self._drain_due_locked(now)
            if due:
                self._due_inbox.extend(due)
                if len(self._due_inbox) > _MAX_DUE_INBOX:
                    self._due_inbox = self._due_inbox[-_MAX_DUE_INBOX:]
            return list(due)

    def take_due(self) -> list[str]:
        """Return queued due reports and clear the inbox (UI poll / chat)."""
        with self._lock:
            due = list(self._due_inbox)
            self._due_inbox.clear()
            return due

    def _drain_due_locked(self, now=None) -> list[str]:
        orch = self.orchestrator
        if orch is not None:
            drain = getattr(orch, "drain_due_automations", None)
            if callable(drain):
                try:
                    return list(drain(now=now) or [])
                except TypeError:
                    return list(drain() or [])
        return run_due_jobs(self._automation_store(), run_prompt=None, now=now)

    def _chat(self, body: bytes) -> UiResponse:
        if len(body) > MAX_BODY_BYTES:
            return _json(413, {"error": "That message is too long."})
        if not self.ready:
            return _json(
                503,
                {
                    "error": self.missing_key
                    or "Jarvis needs one AI API key before chat can start. "
                    "Paste it on this page or add it to .env."
                },
            )
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json(400, {"error": "Send JSON like {\"message\": \"...\"}."})
        if not isinstance(payload, dict):
            return _json(400, {"error": "Send a JSON object with a message field."})
        message = str(payload.get("message") or payload.get("prompt") or "").strip()
        if not message:
            return _json(400, {"error": "Type a message first."})

        with self._lock:
            newly = self._drain_due_locked()
            if newly:
                self._due_inbox.extend(newly)
            due = list(self._due_inbox)
            self._due_inbox.clear()
            memory = getattr(self.orchestrator, "memory", None)
            if isinstance(memory, list):
                memory.append(f"User: {message}")
            reply = self.orchestrator.handle_message(message)
        return _json(200, {"reply": str(reply), "due": [str(item) for item in due]})

    def _accepts_key_install(self) -> bool:
        host = urlparse(self.public_base or "").hostname or DEFAULT_HOST
        return host in _LOOPBACK

    def _install_key(self, body: bytes) -> UiResponse:
        """Save a pasted AI key locally and unlock chat without a restart."""
        if not self._accepts_key_install():
            return _json(403, {"error": "AI keys can only be pasted on localhost."})
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json(400, {"error": "Send JSON like {\"api_key\": \"...\"}."})
        if not isinstance(payload, dict):
            return _json(400, {"error": "Send a JSON object with an api_key field."})
        api_key = payload.get("api_key") or payload.get("key") or ""
        provider = payload.get("provider")
        try:
            settings = install_llm_key(
                str(api_key or ""),
                None if provider is None else str(provider),
                path=self.env_path,
            )
        except InvalidAPIKeyError as exc:
            return _json(400, {"error": str(exc)})
        except MissingAPIKeyError as exc:
            return _json(400, {"error": str(exc)})

        reset_failover_state()
        factory = self.orchestrator_factory or _default_orchestrator_factory()
        try:
            orchestrator = factory(settings)
        except Exception:
            return _json(
                500,
                {
                    "error": (
                        "Saved the key to .env, but Jarvis could not start chat. "
                        "Restart python main.py --serve."
                    ),
                    "saved": True,
                },
            )

        with self._lock:
            previous = self.orchestrator
            self.orchestrator = orchestrator
            self.settings = settings
            self.missing_key = None
            self.auth_store = _resolve_auth_store(orchestrator, self.auth_store)
            if previous is not None and previous is not orchestrator:
                closer = getattr(previous, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        pass

        status = self.status_payload()
        return _json(
            200,
            {
                "ok": True,
                "ready": True,
                "message": (
                    f"Using {settings.summary()}. Chat is unlocked — no restart needed. "
                    "The key stays on this machine in .env (gitignored)."
                ),
                "llm": status.get("llm"),
                "runtime": status.get("runtime"),
                "google": status.get("google"),
                "auth": status.get("auth"),
            },
        )

    def _connect_google(self) -> UiResponse:
        """Start PKCE login. Works without an AI key — OAuth is not a vendor key."""
        if not google_client_id():
            return _json(
                400,
                {
                    "error": missing_client_id_message(),
                    "configured": False,
                },
            )
        redirect_uri = f"{self.public_base}/oauth/google/callback"
        try:
            login = begin_google_login(redirect_uri)
        except OAuthError as exc:
            return _json(400, {"error": str(exc), "configured": False})
        with self._lock:
            self._google_login = login
        return _json(
            200,
            {
                "ok": True,
                "auth_url": login.auth_url,
                "redirect_uri": login.redirect_uri,
                "message": (
                    "Open Google to connect mail and calendar. "
                    "This is OAuth, not a second AI key."
                ),
            },
        )

    def _disconnect_google(self) -> UiResponse:
        with self._lock:
            self._google_login = None
            message = disconnect_google(self.auth_store)
        return _json(
            200,
            {
                "ok": True,
                "message": message,
                "auth": self.auth_store.status_line(),
                "google": self._google_status(),
            },
        )

    def _google_callback(self, path: str) -> UiResponse:
        with self._lock:
            login = self._google_login
        if login is None:
            return _oauth_page(
                400,
                "No Google login is in progress. Return to Jarvis and click Connect Google.",
            )
        try:
            code = parse_oauth_callback(path, login.state)
            account = finish_google_login(self.auth_store, login, code)
        except OAuthError as exc:
            return _oauth_page(400, str(exc))
        with self._lock:
            if self._google_login is login:
                self._google_login = None
        who = account.email or "your Google account"
        return _oauth_page(
            200,
            f"Google is connected as {who}. Mail and calendar are ready — still no extra AI key.",
            redirect="/",
        )

    def make_server(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        handler = _bound_handler(self)
        return ThreadingHTTPServer((host, port), handler)


def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    open_browser: bool = False,
    orchestrator=None,
    settings: LLMSettings | None = None,
    missing_key: str | None = None,
    ready: Callable[[str], None] | None = None,
) -> None:
    """Block until KeyboardInterrupt, serving the UI on loopback."""
    if host not in _LOOPBACK:
        print(
            f"Warning: binding to {host} exposes Jarvis beyond this machine. "
            "Prefer 127.0.0.1."
        )
    app = JarvisWebApp(
        orchestrator=orchestrator,
        settings=settings,
        missing_key=missing_key,
        auth_store=getattr(orchestrator, "auth_store", None)
        if orchestrator is not None
        else None,
    )
    httpd = app.make_server(host, port)
    url = _public_url(host, httpd.server_address[1])
    app.public_base = url.rstrip("/")
    print(f"Jarvis UI · {url}", flush=True)
    if missing_key:
        print(
            "Chat is paused until you paste one AI API key in the page "
            "or add it to .env.",
            flush=True,
        )
    elif settings is not None:
        print(f"Same key as the REPL · {settings.summary()}", flush=True)
    print(
        "Connect Google from this page (OAuth, not a second AI key). "
        "Reminders fire in the background while this UI is open "
        f"(every {int(app.tick_seconds)}s) — no extra cron. "
        "Only localhost can connect. Ctrl+C to stop.",
        flush=True,
    )
    if ready is not None:
        ready(url)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    app.start_ticker()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nJarvis UI stopped.")
    finally:
        app.stop_ticker()
        httpd.server_close()


def _tick_seconds() -> float:
    raw = (os.getenv("JARVIS_AUTOMATION_TICK_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TICK_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TICK_SECONDS
    return max(1.0, min(value, 3600.0))


def _public_url(host: str, port: int) -> str:
    display = "127.0.0.1" if host in {"0.0.0.0", "::", "::0"} else host
    return f"http://{display}:{port}/"


def _index_html() -> bytes:
    return _INDEX_PATH.read_bytes()


def _fallback_summaries(primary: LLMSettings) -> list[dict]:
    try:
        configured = list_llm_settings()
    except MissingAPIKeyError:
        return []
    return [
        {
            "provider": item.provider,
            "model": item.model,
            "summary": item.summary(),
        }
        for item in configured
        if item.provider != primary.provider
    ]


def _default_orchestrator_factory():
    from src.assistant import build_orchestrator

    return build_orchestrator


def _resolve_auth_store(orchestrator, auth_store: AuthStore | None) -> AuthStore:
    if isinstance(auth_store, AuthStore):
        return auth_store
    stored = getattr(orchestrator, "auth_store", None) if orchestrator is not None else None
    if isinstance(stored, AuthStore):
        return stored
    return AuthStore()


def _oauth_page(status: int, message: str, redirect: str | None = None) -> UiResponse:
    safe = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    refresh = (
        f'<meta http-equiv="refresh" content="1;url={redirect}">' if redirect else ""
    )
    link = f'<p><a href="{redirect}">Continue to Jarvis</a></p>' if redirect else ""
    html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"{refresh}<title>Jarvis</title></head><body>"
        f"<p>{safe}</p>{link}</body></html>"
    )
    return UiResponse(status, html.encode("utf-8"), "text/html; charset=utf-8")


def _json(status: int, payload: dict) -> UiResponse:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return UiResponse(status, raw)


def _bound_handler(app: JarvisWebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._handle()

        def do_POST(self) -> None:  # noqa: N802
            self._handle()

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle()

        def _handle(self) -> None:
            length = 0
            raw_len = self.headers.get("Content-Length")
            if raw_len:
                try:
                    length = max(0, int(raw_len))
                except ValueError:
                    length = 0
            if length > MAX_BODY_BYTES:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 8192))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self._write(_json(413, {"error": "That message is too long."}))
                return
            body = self.rfile.read(length) if length else b""
            response = app.dispatch(self.command, self.path, body)
            self._write(response, send_body=self.command != "HEAD")

        def _write(self, response: UiResponse, *, send_body: bool = True) -> None:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if send_body:
                self.wfile.write(response.body)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    return Handler
