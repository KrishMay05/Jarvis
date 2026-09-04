"""Adapt a remote MCP tool to the Jarvis Tool interface."""

from __future__ import annotations

import json
from typing import Any

from src.mcp.session import McpError, McpSession
from src.tools.base_tool import Tool


class McpTool(Tool):
    def __init__(
        self,
        session: McpSession,
        tool_def: dict[str, Any],
        public_name: str,
        aliases: tuple[str, ...] = (),
    ):
        self._session = session
        self._def = tool_def
        self._public_name = public_name
        self._aliases = aliases

    def name(self) -> str:
        return self._public_name

    def aliases(self) -> tuple[str, ...]:
        return self._aliases

    def description(self) -> str:
        summary = (self._def.get("description") or "").strip() or "MCP tool"
        schema = self._def.get("inputSchema") or {}
        schema_text = json.dumps(schema, separators=(",", ":")) if schema else "{}"
        return (
            f"[MCP:{self._session.name}] {summary} "
            f"Call with action '{self._public_name}'. "
            f"Arguments JSON schema: {schema_text}"
        )

    def use(self, args: Any) -> str:
        arguments = normalize_mcp_arguments(args, self._def.get("inputSchema"))
        try:
            result = self._session.call_tool(str(self._def["name"]), arguments)
        except McpError as exc:
            return f"MCP tool '{self._public_name}' failed: {exc}"
        return format_call_result(result)


def normalize_mcp_arguments(args: Any, input_schema: Any) -> dict[str, Any]:
    schema = input_schema if isinstance(input_schema, dict) else {}
    if args is None:
        return {}
    if isinstance(args, dict):
        for wrapper in ("arguments", "args", "input", "params"):
            if wrapper in args and len(args) == 1:
                inner = args[wrapper]
                if isinstance(inner, dict):
                    return inner
                if isinstance(inner, str):
                    return _string_to_args(inner, schema)
        return args
    if isinstance(args, str):
        stripped = args.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        return _string_to_args(stripped, schema)
    return {"value": args}


def format_call_result(result: Any) -> str:
    if result is None:
        return "(empty MCP response)"
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return json.dumps(result)

    prefix = "MCP tool error: " if result.get("isError") else ""
    parts: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if text:
                    parts.append(str(text))
            elif isinstance(item, dict):
                parts.append(json.dumps(item))
            elif item:
                parts.append(str(item))
    body = "\n".join(parts) if parts else json.dumps(result)
    return prefix + body


def _string_to_args(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    if len(required) == 1:
        return {str(required[0]): text}
    for name in ("query", "text", "input", "q", "message", "path", "name"):
        if name in props:
            return {name: text}
    if len(props) == 1:
        return {str(next(iter(props))): text}
    return {"input": text}
