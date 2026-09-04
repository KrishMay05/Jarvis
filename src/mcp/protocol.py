"""JSON-RPC 2.0 with LSP-style Content-Length framing (MCP stdio transport)."""

from __future__ import annotations

import io
import json
import os
import select
import time
from typing import Any, BinaryIO


PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "jarvis", "version": "0.3"}


class McpProtocolError(RuntimeError):
    """Raised when the MCP stdio stream is invalid or times out."""


def encode_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


class MessageReader:
    """Read framed MCP messages from a binary stream with a timeout."""

    def __init__(self, stream: BinaryIO, timeout: float = 15.0):
        self._stream = stream
        self._timeout = timeout
        self._buffer = bytearray()

    def read(self, timeout: float | None = None) -> dict[str, Any]:
        wait = self._timeout if timeout is None else timeout
        headers = self._read_headers(wait)
        length_text = headers.get("content-length")
        if not length_text:
            raise McpProtocolError("MCP message missing Content-Length header")
        try:
            length = int(length_text)
        except ValueError as exc:
            raise McpProtocolError(f"Invalid Content-Length: {length_text}") from exc
        if length < 0 or length > 8_000_000:
            raise McpProtocolError(f"Unreasonable Content-Length: {length}")
        body = self._read_exact(length, wait)
        try:
            parsed = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise McpProtocolError("MCP message body is not JSON") from exc
        if not isinstance(parsed, dict):
            raise McpProtocolError("MCP message body must be a JSON object")
        return parsed

    def _read_headers(self, timeout: float) -> dict[str, str]:
        headers: dict[str, str] = {}
        while True:
            line = self._readline(timeout)
            if line in (b"\r\n", b"\n", b""):
                break
            decoded = line.decode("ascii", errors="replace").strip()
            if ":" not in decoded:
                raise McpProtocolError(f"Malformed MCP header line: {decoded!r}")
            key, value = decoded.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        return headers

    def _readline(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            self._fill(max(remaining, 0.0))
        idx = self._buffer.index(b"\n") + 1
        line = bytes(self._buffer[:idx])
        del self._buffer[:idx]
        return line

    def _read_exact(self, n: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while len(self._buffer) < n:
            remaining = deadline - time.monotonic()
            self._fill(max(remaining, 0.0))
        data = bytes(self._buffer[:n])
        del self._buffer[:n]
        return data

    def _fill(self, remaining: float) -> None:
        if remaining <= 0:
            raise McpProtocolError("Timed out waiting for MCP server")
        fd = self._fileno()
        if fd is not None:
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise McpProtocolError("Timed out waiting for MCP server")
            chunk = os.read(fd, 4096)
        else:
            chunk = self._stream.read(4096)
        if not chunk:
            raise McpProtocolError("MCP server closed stdout")
        self._buffer.extend(chunk)

    def _fileno(self) -> int | None:
        fileno = getattr(self._stream, "fileno", None)
        if not callable(fileno):
            return None
        try:
            return fileno()
        except (AttributeError, OSError, io.UnsupportedOperation):
            return None
