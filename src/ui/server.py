"""Localhost web UI for Jarvis — no extra API key.

Bind to loopback by default so the assistant is not exposed on the LAN.
Chat still uses the same Gemini/OpenAI/Anthropic key as the REPL.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from src.auth.store import AuthStore, auth_status_line
from src.automation.store import AutomationStore, automation_status_line
from src.config import LLMSettings, MissingAPIKeyError, describe_runtime
from src.mcp.config import mcp_status_line
from src.memory.store import MemoryStore, memory_status_line

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_BODY_BYTES = 32_768
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
    ):
        self.orchestrator = orchestrator
        self.settings = settings
        self.missing_key = missing_key
        self._lock = threading.Lock()

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
        if verb == "POST" and route == "/api/chat":
            return self._chat(body)
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
            "auth": auth_status_line(
                getattr(orch, "auth_store", None)
                if orch is not None and isinstance(getattr(orch, "auth_store", None), AuthStore)
                else None
            ),
            "mcp": mcp_status_line(),
            "bind": "localhost only — not exposed on your LAN",
        }

    def _automation_text(self) -> str:
        orch = self.orchestrator
        store = getattr(orch, "automation_store", None) if orch is not None else None
        if isinstance(store, AutomationStore):
            return store.format_list()
        return AutomationStore().format_list()

    def _chat(self, body: bytes) -> UiResponse:
        if len(body) > MAX_BODY_BYTES:
            return _json(413, {"error": "That message is too long."})
        if not self.ready:
            return _json(
                503,
                {
                    "error": self.missing_key
                    or "Jarvis needs one AI API key in .env before chat can start."
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
            due: list[str] = []
            drain = getattr(self.orchestrator, "drain_due_automations", None)
            if callable(drain):
                due = list(drain() or [])
            memory = getattr(self.orchestrator, "memory", None)
            if isinstance(memory, list):
                memory.append(f"User: {message}")
            reply = self.orchestrator.handle_message(message)
        return _json(200, {"reply": str(reply), "due": [str(item) for item in due]})

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
    )
    httpd = app.make_server(host, port)
    url = _public_url(host, httpd.server_address[1])
    print(f"Jarvis UI · {url}", flush=True)
    if missing_key:
        print("Chat is paused until you add one AI API key to .env.", flush=True)
    elif settings is not None:
        print(f"Same key as the REPL · {settings.summary()}", flush=True)
    print("Only localhost can connect. Ctrl+C to stop.", flush=True)
    if ready is not None:
        ready(url)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nJarvis UI stopped.")
    finally:
        httpd.server_close()


def _public_url(host: str, port: int) -> str:
    display = "127.0.0.1" if host in {"0.0.0.0", "::", "::0"} else host
    return f"http://{display}:{port}/"


def _index_html() -> bytes:
    return _INDEX_PATH.read_bytes()


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
            if self.command == "HEAD":
                self._write(app.dispatch("GET", self.path, b""), send_body=False)
                return
            self._write(app.dispatch(self.command, self.path, body))

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
