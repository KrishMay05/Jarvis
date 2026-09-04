"""Load Claude-Desktop-compatible MCP server configs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    disabled: bool = False


@dataclass(frozen=True)
class McpConfig:
    path: Path | None
    servers: tuple[McpServerSpec, ...] = ()

    @property
    def enabled_servers(self) -> tuple[McpServerSpec, ...]:
        return tuple(s for s in self.servers if not s.disabled)


def config_search_paths() -> list[Path]:
    """Paths Jarvis checks, first match wins.

    ``JARVIS_MCP_CONFIG`` is exclusive: if it is set, only that file is used
    (missing file means no servers, not a fallback).
    """
    explicit = (os.getenv("JARVIS_MCP_CONFIG") or "").strip()
    if explicit:
        return [Path(explicit).expanduser()]
    return [Path.cwd() / "mcp.json", Path.home() / ".jarvis" / "mcp.json"]


def resolve_mcp_config_path() -> Path | None:
    for path in config_search_paths():
        if path.is_file():
            return path
    return None


def load_mcp_config(path: Path | None = None) -> McpConfig:
    """Parse an MCP config file. Missing/empty files yield zero servers."""
    target = path if path is not None else resolve_mcp_config_path()
    if target is None or not target.is_file():
        return McpConfig(path=target if target is not None else None, servers=())

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return McpConfig(path=target, servers=())

    if not isinstance(raw, dict):
        return McpConfig(path=target, servers=())

    block = raw.get("mcpServers")
    if block is None:
        block = raw.get("servers")
    if not isinstance(block, dict):
        return McpConfig(path=target, servers=())

    servers: list[McpServerSpec] = []
    for name, spec in block.items():
        parsed = _parse_server(str(name), spec)
        if parsed is not None:
            servers.append(parsed)
    return McpConfig(path=target, servers=tuple(servers))


def mcp_status_line(config: McpConfig | None = None) -> str:
    config = config if config is not None else load_mcp_config()
    enabled = config.enabled_servers
    if not enabled:
        searched = " or ".join(str(p) for p in config_search_paths())
        return (
            f"MCP: none configured (add mcp.json at {searched} "
            "to connect third-party tools without extra AI keys)"
        )
    names = ", ".join(s.name for s in enabled)
    origin = config.path or "mcp.json"
    return f"MCP: {names} (from {origin})"


def _parse_server(name: str, spec: object) -> McpServerSpec | None:
    if not isinstance(spec, dict):
        return None
    command = spec.get("command")
    if not command or not isinstance(command, str):
        return None
    raw_args = spec.get("args") or []
    if not isinstance(raw_args, list):
        return None
    args = tuple(str(item) for item in raw_args)
    raw_env = spec.get("env") or {}
    env = (
        {str(k): str(v) for k, v in raw_env.items()}
        if isinstance(raw_env, dict)
        else {}
    )
    cwd = spec.get("cwd")
    cwd_text = str(cwd) if cwd else None
    disabled = bool(spec.get("disabled"))
    return McpServerSpec(
        name=name,
        command=command,
        args=args,
        env=env,
        cwd=cwd_text,
        disabled=disabled,
    )
