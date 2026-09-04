"""Spawn one MCP stdio server and call its tools."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections import deque
from typing import Any

from src.mcp.config import McpServerSpec
from src.mcp.protocol import (
    CLIENT_INFO,
    PROTOCOL_VERSION,
    MessageReader,
    McpProtocolError,
    encode_message,
)


class McpError(RuntimeError):
    """JSON-RPC or lifecycle error from an MCP server."""


class McpSession:
    def __init__(
        self,
        spec: McpServerSpec,
        *,
        initialize_timeout: float = 20.0,
        call_timeout: float = 30.0,
    ):
        self.spec = spec
        self.initialize_timeout = initialize_timeout
        self.call_timeout = call_timeout
        self.server_info: dict[str, Any] = {}
        self.tools: list[dict[str, Any]] = []
        self._proc: subprocess.Popen[bytes] | None = None
        self._reader: MessageReader | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=40)
        self._stderr_thread: threading.Thread | None = None

    @property
    def name(self) -> str:
        return self.spec.name

    def start(self) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env.update(self.spec.env)
        argv = [self.spec.command, *self.spec.args]
        cwd = os.path.expanduser(self.spec.cwd) if self.spec.cwd else None
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=cwd,
                bufsize=0,
            )
        except OSError as exc:
            raise McpError(
                f"Failed to start MCP server '{self.spec.name}': {exc}"
            ) from exc

        assert self._proc.stdout is not None
        assert self._proc.stderr is not None
        self._reader = MessageReader(self._proc.stdout, timeout=self.initialize_timeout)
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name=f"mcp-stderr-{self.spec.name}",
            daemon=True,
        )
        self._stderr_thread.start()
        try:
            self._handshake()
        except Exception:
            self.close()
            raise

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        return self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
            timeout=self.call_timeout,
        )

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        self._reader = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    def _handshake(self) -> None:
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            timeout=self.initialize_timeout,
        )
        if isinstance(result, dict):
            info = result.get("serverInfo")
            if isinstance(info, dict):
                self.server_info = info
        self._notify("notifications/initialized", {})
        listed = self._request("tools/list", {}, timeout=self.initialize_timeout)
        tools = listed.get("tools") if isinstance(listed, dict) else None
        self.tools = [t for t in tools or [] if isinstance(t, dict) and t.get("name")]

    def _request(
        self, method: str, params: dict[str, Any], timeout: float
    ) -> Any:
        with self._lock:
            msg_id = self._next_id
            self._next_id += 1
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "method": method,
                    "params": params,
                }
            )
            deadline_error = None
            try:
                while True:
                    message = self._read(timeout)
                    if message.get("id") != msg_id:
                        continue
                    if "error" in message:
                        error = message["error"]
                        if isinstance(error, dict):
                            text = error.get("message") or json.dumps(error)
                        else:
                            text = str(error)
                        raise McpError(f"{self.spec.name} {method}: {text}")
                    return message.get("result")
            except McpProtocolError as exc:
                deadline_error = exc
            tail = self.stderr_tail()
            extra = f" Stderr:\n{tail}" if tail else ""
            raise McpError(
                f"MCP server '{self.spec.name}' failed during {method}.{extra}"
            ) from deadline_error

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        with self._lock:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                }
            )

    def _write(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpError(f"MCP server '{self.spec.name}' is not running")
        try:
            proc.stdin.write(encode_message(payload))
            proc.stdin.flush()
        except OSError as exc:
            raise McpError(
                f"Could not write to MCP server '{self.spec.name}': {exc}"
            ) from exc

    def _read(self, timeout: float) -> dict[str, Any]:
        if self._reader is None:
            raise McpError(f"MCP server '{self.spec.name}' is not running")
        return self._reader.read(timeout=timeout)

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._stderr.append(text)
        except OSError:
            return
