"""Localhost web UI for Jarvis — no extra API key.

Bind to loopback by default so the assistant is not exposed on the LAN.
Chat still uses the same Gemini/OpenAI/Anthropic key as the REPL.
"""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from src.assistant import attach_mcp, mcp_manager_of
from src.auth.oauth import (
    OAuthError,
    begin_google_login,
    disconnect_google,
    finish_google_login,
    google_client_id,
    google_client_secret,
    missing_client_id_message,
    parse_oauth_callback,
)
from src.auth.store import AuthStore, auth_status_line
from src.automation.runner import run_due_jobs
from src.automation.schedule import ScheduleParseError, parse_schedule
from src.automation.store import AutomationStore, automation_status_line
from src.config import (
    InvalidAPIKeyError,
    InvalidGoogleClientError,
    LLMSettings,
    MissingAPIKeyError,
    describe_runtime,
    install_google_oauth_config,
    install_llm_key,
    list_llm_settings,
)
from src.llm import reset_failover_state
from src.mcp.config import (
    McpConfigError,
    load_mcp_config,
    mcp_payload,
    mcp_status_line,
    remove_mcp_server,
    set_mcp_server_disabled,
    upsert_mcp_server,
)
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
            return _json(200, self._automations_payload())
        if verb == "GET" and route == "/api/memory":
            return _json(200, self._memory_payload())
        if verb == "GET" and route == "/api/due":
            return _json(200, {"due": self.take_due()})
        if verb == "GET" and route == "/api/chat/history":
            return _json(200, self._chat_history_payload())
        if verb == "POST" and route == "/api/chat/history/clear":
            return self._clear_chat_history()
        if verb == "GET" and route == "/api/mcp":
            return _json(200, self._mcp_payload())
        if verb == "POST" and route == "/api/chat":
            return self._chat(body)
        if verb == "POST" and route == "/api/chat/stream":
            error, chunks = self.begin_chat_stream(body)
            if error is not None:
                return error
            return UiResponse(
                200,
                b"".join(chunks),
                "text/event-stream; charset=utf-8",
            )
        if verb == "POST" and route == "/api/key":
            return self._install_key(body)
        if verb == "POST" and route == "/api/memory":
            return self._remember_fact(body)
        if verb == "POST" and route == "/api/memory/forget":
            return self._forget_fact(body)
        if verb == "POST" and route == "/api/automations":
            return self._add_automation(body)
        if verb == "POST" and route == "/api/automations/cancel":
            return self._cancel_automation(body)
        if verb == "POST" and route == "/api/automations/pause":
            return self._set_automation_enabled(body, False)
        if verb == "POST" and route == "/api/automations/enable":
            return self._set_automation_enabled(body, True)
        if verb == "POST" and route == "/api/mcp":
            return self._upsert_mcp(body)
        if verb == "POST" and route == "/api/mcp/remove":
            return self._remove_mcp(body)
        if verb == "POST" and route == "/api/mcp/disable":
            return self._set_mcp_disabled(body, True)
        if verb == "POST" and route == "/api/mcp/enable":
            return self._set_mcp_disabled(body, False)
        if verb == "POST" and route == "/api/mcp/reload":
            return self._reload_mcp()
        if verb == "POST" and route == "/api/auth/google/config":
            return self._install_google_oauth(body)
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
            "/api/memory",
            "/api/mcp",
            "/api/due",
            "/api/chat/history",
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
            "memory_facts": self._memory_payload()["facts"],
            "automations": automation_status_line(
                automations if isinstance(automations, AutomationStore) else None
            ),
            "automation_jobs": self._automations_payload()["jobs"],
            "auth": auth_status_line(self.auth_store),
            "google": self._google_status(),
            "mcp": mcp_status_line(),
            "mcp_servers": self._mcp_payload()["servers"],
            "bind": "localhost only — not exposed on your LAN",
            "can_install_key": self._accepts_key_install(),
            "can_install_google": self._accepts_key_install(),
            "scheduler": self._scheduler_status(),
        }

    def _google_status(self) -> dict:
        account = self.auth_store.get("google")
        connected = bool(account is not None and account.access_token)
        return {
            "configured": bool(google_client_id()),
            "connected": connected,
            "email": (account.email if account is not None else "") or "",
            "has_secret": bool(google_client_secret()),
        }

    def _memory_store(self) -> MemoryStore:
        orch = self.orchestrator
        store = getattr(orch, "memory_store", None) if orch is not None else None
        if isinstance(store, MemoryStore):
            return store
        return MemoryStore()

    def _automation_store(self) -> AutomationStore:
        orch = self.orchestrator
        store = getattr(orch, "automation_store", None) if orch is not None else None
        if isinstance(store, AutomationStore):
            return store
        return AutomationStore()

    def _memory_payload(self) -> dict:
        store = self._memory_store()
        return {
            "facts": [fact.to_dict() for fact in store.list_facts()],
            "status": store.status_line(),
        }

    def _chat_history_payload(self) -> dict:
        """Recent user/assistant turns from local memory — no extra API key."""
        store = self._memory_store()
        return {
            "turns": [turn.to_dict() for turn in store.list_turns()],
            "status": store.status_line(),
        }

    def _clear_chat_history(self) -> UiResponse:
        """Drop restored chat turns. Facts stay. Localhost only — no AI key."""
        blocked = self._refuse_remote_writes()
        if blocked is not None:
            return blocked
        with self._lock:
            orch = self.orchestrator
            if orch is not None:
                clearer = getattr(orch, "clear_conversation", None)
                if callable(clearer):
                    message = clearer()
                else:
                    memory = getattr(orch, "memory", None)
                    if isinstance(memory, list):
                        memory.clear()
                    message = self._memory_store().clear_turns()
            else:
                message = self._memory_store().clear_turns()
        data = self._chat_history_payload()
        data.update({"ok": True, "message": message})
        return _json(200, data)

    def _automations_payload(self) -> dict:
        store = self._automation_store()
        return {
            "text": store.format_list(),
            "jobs": [job.to_dict() for job in store.list_jobs()],
            "status": store.status_line(),
        }

    def _local_writes_ok(self) -> bool:
        return self._accepts_key_install()

    def _refuse_remote_writes(self) -> UiResponse | None:
        if self._local_writes_ok():
            return None
        return _json(
            403,
            {"error": "Memory, automations, and MCP can only be edited on localhost."},
        )

    def _remember_fact(self, body: bytes) -> UiResponse:
        blocked = self._refuse_remote_writes()
        if blocked is not None:
            return blocked
        payload, error = _json_object(body, 'Send JSON like {"text": "I live in Austin"}.')
        if error is not None:
            return error
        text = str(payload.get("text") or payload.get("fact") or "").strip()
        if not text:
            return _json(400, {"error": "Type a fact to remember first."})
        with self._lock:
            message = self._memory_store().remember(text)
        data = self._memory_payload()
        data.update({"ok": True, "message": message})
        return _json(200, data)

    def _forget_fact(self, body: bytes) -> UiResponse:
        blocked = self._refuse_remote_writes()
        if blocked is not None:
            return blocked
        payload, error = _json_object(body, 'Send JSON like {"id": "abc123"}.')
        if error is not None:
            return error
        query = str(payload.get("id") or payload.get("query") or "").strip()
        if not query:
            return _json(400, {"error": "Say which fact to forget (id or matching words)."})
        with self._lock:
            message = self._memory_store().forget(query)
        data = self._memory_payload()
        data.update({"ok": True, "message": message})
        status = 200
        if message.startswith("No remembered fact"):
            status = 404
        return _json(status, data)

    def _add_automation(self, body: bytes) -> UiResponse:
        blocked = self._refuse_remote_writes()
        if blocked is not None:
            return blocked
        payload, error = _json_object(
            body,
            'Send JSON like {"kind": "remind", "title": "stretch", "when": "in 10 minutes"}.',
        )
        if error is not None:
            return error
        kind = str(payload.get("kind") or "remind").strip().lower() or "remind"
        title = str(payload.get("title") or payload.get("text") or "").strip()
        when = str(payload.get("when") or payload.get("schedule") or "").strip()
        message = str(payload.get("message") or "").strip()
        prompt = str(payload.get("prompt") or "").strip()
        if kind == "run" and not title:
            title = prompt
        if kind != "run" and not title:
            title = message
        if not title:
            return _json(400, {"error": "Give the automation a title, reminder text, or prompt."})
        if not when:
            return _json(
                400,
                {"error": "Say when to run it, for example 'in 10 minutes' or 'every morning'."},
            )
        try:
            schedule = parse_schedule(when)
            with self._lock:
                job = self._automation_store().add(
                    kind=kind,
                    title=title,
                    schedule=schedule,
                    message=message or (title if kind == "remind" else ""),
                    prompt=prompt or (title if kind == "run" else ""),
                )
        except ScheduleParseError as exc:
            return _json(400, {"error": str(exc)})
        except ValueError as exc:
            return _json(400, {"error": str(exc)})
        data = self._automations_payload()
        data.update(
            {
                "ok": True,
                "job": job.to_dict(),
                "message": f"Scheduled ({job.id}): {job.summary_line().lstrip('- ').strip()}",
            }
        )
        return _json(200, data)

    def _cancel_automation(self, body: bytes) -> UiResponse:
        blocked = self._refuse_remote_writes()
        if blocked is not None:
            return blocked
        payload, error = _json_object(body, 'Send JSON like {"id": "abc123"}.')
        if error is not None:
            return error
        query = str(payload.get("id") or payload.get("query") or "").strip()
        if not query:
            return _json(400, {"error": "Say which automation to cancel (id or matching words)."})
        with self._lock:
            removed = self._automation_store().cancel(query)
        data = self._automations_payload()
        if not removed:
            data.update({"ok": False, "error": f"No automation matched '{query}'."})
            return _json(404, data)
        data.update(
            {
                "ok": True,
                "cancelled": [job.to_dict() for job in removed],
                "message": "Cancelled:\n" + "\n".join(job.summary_line() for job in removed),
            }
        )
        return _json(200, data)

    def _set_automation_enabled(self, body: bytes, enabled: bool) -> UiResponse:
        blocked = self._refuse_remote_writes()
        if blocked is not None:
            return blocked
        payload, error = _json_object(body, 'Send JSON like {"id": "abc123"}.')
        if error is not None:
            return error
        query = str(payload.get("id") or payload.get("query") or "").strip()
        if not query:
            verb = "resume" if enabled else "pause"
            return _json(400, {"error": f"Say which automation to {verb} (id or matching words)."})
        with self._lock:
            changed = self._automation_store().set_enabled(query, enabled)
        data = self._automations_payload()
        if not changed:
            data.update({"ok": False, "error": f"No automation matched '{query}'."})
            return _json(404, data)
        action = "Resumed" if enabled else "Paused"
        data.update(
            {
                "ok": True,
                "jobs": data["jobs"],
                "message": f"{action}:\n" + "\n".join(job.summary_line() for job in changed),
            }
        )
        return _json(200, data)

    def _mcp_payload(self) -> dict:
        return mcp_payload(load_mcp_config(), mcp_manager_of(self.orchestrator))

    def _reconnect_mcp_locked(self) -> None:
        if self.orchestrator is None:
            return
        attach_mcp(self.orchestrator)

    def _upsert_mcp(self, body: bytes) -> UiResponse:
        blocked = self._refuse_remote_writes()
        if blocked is not None:
            return blocked
        payload, error = _json_object(
            body,
            'Send JSON like {"name": "filesystem", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}.',
        )
        if error is not None:
            return error
        name = payload.get("name") or payload.get("id") or ""
        command = payload.get("command") or ""
        args = payload.get("args") if "args" in payload else None
        env = payload.get("env") if "env" in payload else None
        cwd = payload.get("cwd") if "cwd" in payload else None
        disabled = payload.get("disabled")
        try:
            with self._lock:
                config, spec, created = upsert_mcp_server(
                    str(name),
                    str(command),
                    args=args,
                    env=env,
                    cwd=cwd,
                    disabled=None if disabled is None else bool(disabled),
                )
                self._reconnect_mcp_locked()
        except McpConfigError as exc:
            return _json(400, {"error": str(exc)})
        data = self._mcp_payload()
        verb = "Added" if created else "Updated"
        data.update(
            {
                "ok": True,
                "server": spec.public_dict(),
                "message": (
                    f"{verb} MCP server '{spec.name}'. "
                    "No extra AI key — Jarvis will spawn it as a local process."
                ),
            }
        )
        return _json(200, data)

    def _remove_mcp(self, body: bytes) -> UiResponse:
        blocked = self._refuse_remote_writes()
        if blocked is not None:
            return blocked
        payload, error = _json_object(body, 'Send JSON like {"name": "filesystem"}.')
        if error is not None:
            return error
        name = str(payload.get("name") or payload.get("id") or "").strip()
        if not name:
            return _json(400, {"error": "Say which MCP server to remove (name)."})
        with self._lock:
            _config, removed = remove_mcp_server(name)
            if removed is None:
                data = self._mcp_payload()
                data.update({"ok": False, "error": f"No MCP server named '{name}'."})
                return _json(404, data)
            self._reconnect_mcp_locked()
        data = self._mcp_payload()
        data.update(
            {
                "ok": True,
                "removed": removed.public_dict(),
                "message": f"Removed MCP server '{removed.name}'.",
            }
        )
        return _json(200, data)

    def _set_mcp_disabled(self, body: bytes, disabled: bool) -> UiResponse:
        blocked = self._refuse_remote_writes()
        if blocked is not None:
            return blocked
        payload, error = _json_object(body, 'Send JSON like {"name": "filesystem"}.')
        if error is not None:
            return error
        name = str(payload.get("name") or payload.get("id") or "").strip()
        if not name:
            verb = "disable" if disabled else "enable"
            return _json(400, {"error": f"Say which MCP server to {verb} (name)."})
        with self._lock:
            _config, spec = set_mcp_server_disabled(name, disabled)
            if spec is None:
                data = self._mcp_payload()
                data.update({"ok": False, "error": f"No MCP server named '{name}'."})
                return _json(404, data)
            self._reconnect_mcp_locked()
        data = self._mcp_payload()
        action = "Disabled" if disabled else "Enabled"
        data.update(
            {
                "ok": True,
                "server": spec.public_dict(),
                "message": f"{action} MCP server '{spec.name}'.",
            }
        )
        return _json(200, data)

    def _reload_mcp(self) -> UiResponse:
        blocked = self._refuse_remote_writes()
        if blocked is not None:
            return blocked
        with self._lock:
            self._reconnect_mcp_locked()
        data = self._mcp_payload()
        connected = sum(1 for row in data["servers"] if row.get("connected"))
        if self.orchestrator is not None:
            message = (
                f"Reloaded MCP servers ({connected} connected). "
                "No extra AI key — they are local processes from mcp.json."
            )
        else:
            message = (
                "Saved MCP config will connect after you paste an AI key "
                "or restart with a key."
            )
        data.update({"ok": True, "message": message})
        return _json(200, data)

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
        error, message = self._parse_chat_message(body)
        if error is not None:
            return error

        with self._lock:
            due = self._note_user_and_take_due(message)
            reply = self.orchestrator.handle_message(message)
        return _json(200, {"reply": str(reply), "due": due})

    def begin_chat_stream(
        self, body: bytes
    ) -> tuple[UiResponse | None, Iterator[bytes] | None]:
        """Start an SSE chat. Errors return JSON; success yields event chunks."""
        error, message = self._parse_chat_message(body)
        if error is not None:
            return error, None

        def chunks() -> Iterator[bytes]:
            with self._lock:
                due = self._note_user_and_take_due(message)
                if due:
                    yield _sse({"type": "due", "items": due})
                try:
                    yield from self._orchestrator_sse(message)
                except Exception:
                    yield _sse(
                        {
                            "type": "error",
                            "error": "Jarvis could not answer that.",
                        }
                    )

        return None, chunks()

    def _parse_chat_message(self, body: bytes) -> tuple[UiResponse | None, str]:
        if len(body) > MAX_BODY_BYTES:
            return _json(413, {"error": "That message is too long."}), ""
        if not self.ready:
            return (
                _json(
                    503,
                    {
                        "error": self.missing_key
                        or "Jarvis needs one AI API key before chat can start. "
                        "Paste it on this page or add it to .env."
                    },
                ),
                "",
            )
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json(400, {"error": "Send JSON like {\"message\": \"...\"}."}), ""
        if not isinstance(payload, dict):
            return _json(400, {"error": "Send a JSON object with a message field."}), ""
        message = str(payload.get("message") or payload.get("prompt") or "").strip()
        if not message:
            return _json(400, {"error": "Type a message first."}), ""
        return None, message

    def _note_user_and_take_due(self, message: str) -> list[str]:
        newly = self._drain_due_locked()
        if newly:
            self._due_inbox.extend(newly)
        due = [str(item) for item in self._due_inbox]
        self._due_inbox.clear()
        memory = getattr(self.orchestrator, "memory", None)
        if isinstance(memory, list):
            memory.append(f"User: {message}")
        return due

    def _orchestrator_sse(self, message: str) -> Iterator[bytes]:
        orch = self.orchestrator
        streamer = getattr(orch, "handle_message_events", None)
        saw_done = False
        reply = ""
        if callable(streamer):
            for event in streamer(message):
                public = _public_chat_event(event)
                if public is None:
                    continue
                if public.get("type") == "done":
                    saw_done = True
                    reply = str(public.get("reply") or "")
                yield _sse(public)
        else:
            reply = str(orch.handle_message(message))
            if reply:
                yield _sse({"type": "token", "text": reply})
        if not saw_done:
            yield _sse({"type": "done", "reply": reply})

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

    def _install_google_oauth(self, body: bytes) -> UiResponse:
        """Save a pasted Google OAuth client ID locally — no restart, not an AI key."""
        if not self._accepts_key_install():
            return _json(
                403,
                {"error": "Google OAuth client IDs can only be pasted on localhost."},
            )
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json(
                400,
                {"error": "Send JSON like {\"client_id\": \"....apps.googleusercontent.com\"}."},
            )
        if not isinstance(payload, dict):
            return _json(400, {"error": "Send a JSON object with a client_id field."})
        client_id = (
            payload.get("client_id")
            or payload.get("GOOGLE_OAUTH_CLIENT_ID")
            or payload.get("clientId")
            or ""
        )
        client_secret = payload.get("client_secret")
        if client_secret is None:
            client_secret = payload.get("GOOGLE_OAUTH_CLIENT_SECRET")
        try:
            install_google_oauth_config(
                str(client_id or ""),
                None if client_secret is None else str(client_secret),
                path=self.env_path,
            )
        except InvalidGoogleClientError as exc:
            return _json(400, {"error": str(exc), "configured": False})

        status = self.status_payload()
        google = status.get("google") or {}
        return _json(
            200,
            {
                "ok": True,
                "configured": True,
                "has_secret": bool(google.get("has_secret")),
                "message": (
                    "Google OAuth client saved on this machine in .env (gitignored). "
                    "Connect Google when you are ready — no restart, still not a second AI key."
                ),
                "google": google,
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
        "Paste a Google OAuth client ID on this page if .env does not have one yet "
        "(OAuth, not a second AI key — no restart). "
        "Remember facts, schedule automations, and connect MCP servers in the sidebar — no extra API key. "
        "Reminders fire in the background while this UI is open "
        f"(every {int(app.tick_seconds)}s) — no extra cron. "
        "Chat replies stream live — no extra API key. "
        "Refreshing the page restores recent conversation from local memory. "
        "Clear conversation in the page when you want a fresh thread — facts stay. "
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


def _json_object(body: bytes, hint: str) -> tuple[dict | None, UiResponse | None]:
    try:
        payload = json.loads((body or b"").decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _json(400, {"error": hint})
    if not isinstance(payload, dict):
        return None, _json(400, {"error": hint})
    return payload, None


def _json(status: int, payload: dict) -> UiResponse:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return UiResponse(status, raw)


def _sse(event: dict) -> bytes:
    payload = json.dumps(event, ensure_ascii=False)
    return f"data: {payload}\n\n".encode("utf-8")


def _public_chat_event(event) -> dict | None:
    if not isinstance(event, dict):
        return None
    kind = str(event.get("type") or "").strip().lower()
    if kind == "status":
        text = str(event.get("text") or "").strip()
        return {"type": "status", "text": text} if text else None
    if kind == "step":
        agent = str(event.get("agent") or "Agent").strip() or "Agent"
        text = str(event.get("text") or "")
        return {"type": "step", "agent": agent, "text": text}
    if kind == "token":
        return {"type": "token", "text": str(event.get("text") or "")}
    if kind == "due":
        items = event.get("items") or event.get("due") or []
        if not isinstance(items, list):
            items = [items]
        return {"type": "due", "items": [str(item) for item in items]}
    if kind == "done":
        return {"type": "done", "reply": str(event.get("reply") or "")}
    if kind == "error":
        return {
            "type": "error",
            "error": str(event.get("error") or "Jarvis could not answer that."),
        }
    return None


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
            route = urlparse(self.path or "/").path or "/"
            if route != "/" and route.endswith("/"):
                route = route.rstrip("/")
            if self.command == "POST" and route == "/api/chat/stream":
                error, chunks = app.begin_chat_stream(body)
                if error is not None:
                    self._write(error)
                    return
                self._write_sse(chunks)
                return
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

        def _write_sse(self, chunks: Iterator[bytes]) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                for chunk in chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except BrokenPipeError:
                return

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    return Handler
