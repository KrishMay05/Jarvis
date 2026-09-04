#!/usr/bin/env python3
"""Minimal MCP stdio server used by unit tests.

Speaks JSON-RPC 2.0 with Content-Length framing. Tools: echo, add.
"""

from __future__ import annotations

import json
import sys
from typing import Any

TOOLS = [
    {
        "name": "echo",
        "description": "Return the provided text unchanged.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    },
]


def _send(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def _read_headers() -> dict[str, str] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("ascii", errors="replace").strip()
        if ":" not in decoded:
            continue
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def _read_message() -> dict[str, Any] | None:
    headers = _read_headers()
    if headers is None:
        return None
    length = int(headers.get("content-length") or "0")
    body = sys.stdin.buffer.read(length) if length else b"{}"
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        return None
    return parsed


def _result(msg_id: Any, result: Any) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id: Any, message: str) -> None:
    _send(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32000, "message": message},
        }
    )


def _handle_call(arguments: Any, name: str) -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    if name == "echo":
        text = str(args.get("text") or args.get("input") or "")
        return {"content": [{"type": "text", "text": text}]}
    if name == "add":
        try:
            total = float(args["a"]) + float(args["b"])
        except (KeyError, TypeError, ValueError):
            return {
                "content": [{"type": "text", "text": "add requires numeric a and b"}],
                "isError": True,
            }
        if total.is_integer():
            total = int(total)
        return {"content": [{"type": "text", "text": str(total)}]}
    return {
        "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
        "isError": True,
    }


def main() -> None:
    while True:
        message = _read_message()
        if message is None:
            return
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            _result(
                msg_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "jarvis-fake-mcp", "version": "0.1"},
                },
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _result(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            name = str(params.get("name") or "")
            _result(msg_id, _handle_call(params.get("arguments"), name))
        elif msg_id is not None:
            _error(msg_id, f"Unknown method: {method}")


if __name__ == "__main__":
    main()
