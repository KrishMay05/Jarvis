"""Load and save Claude-Desktop-compatible MCP server configs."""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
from dataclasses import dataclass, field
from pathlib import Path


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_ARGS = 64
_MAX_ENV = 32
_MAX_COMMAND = 500
_MAX_ARG = 500
_MAX_ENV_VALUE = 2000


class McpConfigError(ValueError):
    """User-facing error when adding or editing an MCP server."""


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    disabled: bool = False

    def to_json(self) -> dict:
        data: dict = {"command": self.command}
        if self.args:
            data["args"] = list(self.args)
        if self.env:
            data["env"] = dict(self.env)
        if self.cwd:
            data["cwd"] = self.cwd
        if self.disabled:
            data["disabled"] = True
        return data

    def public_dict(self) -> dict:
        """JSON for the UI — env values stay on disk, not in the response."""
        return {
            "name": self.name,
            "command": self.command,
            "args": list(self.args),
            "cwd": self.cwd or "",
            "disabled": self.disabled,
            "env_keys": sorted(self.env),
        }


@dataclass(frozen=True)
class McpConfig:
    path: Path | None
    servers: tuple[McpServerSpec, ...] = ()

    @property
    def enabled_servers(self) -> tuple[McpServerSpec, ...]:
        return tuple(s for s in self.servers if not s.disabled)

    def get(self, name: str) -> McpServerSpec | None:
        needle = name.strip().lower()
        for spec in self.servers:
            if spec.name.lower() == needle:
                return spec
        return None


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


def writable_mcp_config_path() -> Path:
    """Where the UI/CLI writes ``mcp.json``.

    Prefer an explicit ``JARVIS_MCP_CONFIG``, then an existing file in the
    search path, then ``~/.jarvis/mcp.json`` (or ``JARVIS_HOME``).
    """
    explicit = (os.getenv("JARVIS_MCP_CONFIG") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    for path in config_search_paths():
        if path.is_file():
            return path
    home = (os.getenv("JARVIS_HOME") or "").strip()
    base = Path(home).expanduser() if home else Path.home() / ".jarvis"
    return base / "mcp.json"


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


def save_mcp_config(config: McpConfig, path: Path | None = None) -> Path:
    """Write Claude-style ``mcp.json`` (mode 0600). No extra AI key."""
    target = path or config.path or writable_mcp_config_path()
    target = Path(target).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    extra = _read_raw_object(target)
    extra.pop("servers", None)
    extra["mcpServers"] = {spec.name: spec.to_json() for spec in config.servers}
    payload = json.dumps(extra, indent=2, ensure_ascii=False) + "\n"
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, target)
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    return target


def upsert_mcp_server(
    name: str,
    command: str,
    *,
    args: object = None,
    env: object = None,
    cwd: object = None,
    disabled: bool | None = None,
    path: Path | None = None,
) -> tuple[McpConfig, McpServerSpec, bool]:
    """Add or replace one server and persist. Returns (config, spec, created)."""
    ident = validate_mcp_name(name)
    cmd = validate_mcp_command(command)
    target = path or writable_mcp_config_path()
    config = load_mcp_config(target if Path(target).is_file() else target)
    existing = config.get(ident)
    parsed_args = (
        parse_mcp_args(args)
        if args is not None
        else (existing.args if existing is not None else ())
    )
    parsed_env = (
        parse_mcp_env(env)
        if env is not None
        else (dict(existing.env) if existing is not None else {})
    )
    parsed_cwd = _parse_cwd(cwd, existing.cwd if existing is not None else None)
    is_disabled = (
        bool(disabled)
        if disabled is not None
        else (existing.disabled if existing is not None else False)
    )
    spec = McpServerSpec(
        name=ident,
        command=cmd,
        args=parsed_args,
        env=parsed_env,
        cwd=parsed_cwd,
        disabled=is_disabled,
    )
    servers = [item for item in config.servers if item.name.lower() != ident.lower()]
    servers.append(spec)
    saved = McpConfig(path=Path(target), servers=tuple(servers))
    written = save_mcp_config(saved, Path(target))
    return McpConfig(path=written, servers=saved.servers), spec, existing is None


def remove_mcp_server(
    name: str, path: Path | None = None
) -> tuple[McpConfig, McpServerSpec | None]:
    target = path or writable_mcp_config_path()
    config = load_mcp_config(target if Path(target).is_file() else target)
    needle = str(name or "").strip().lower()
    removed = config.get(name)
    if removed is None:
        return config, None
    servers = tuple(item for item in config.servers if item.name.lower() != needle)
    saved = McpConfig(path=Path(target), servers=servers)
    written = save_mcp_config(saved, Path(target))
    return McpConfig(path=written, servers=servers), removed


def set_mcp_server_disabled(
    name: str, disabled: bool, path: Path | None = None
) -> tuple[McpConfig, McpServerSpec | None]:
    target = path or writable_mcp_config_path()
    config = load_mcp_config(target if Path(target).is_file() else target)
    existing = config.get(name)
    if existing is None:
        return config, None
    spec = McpServerSpec(
        name=existing.name,
        command=existing.command,
        args=existing.args,
        env=dict(existing.env),
        cwd=existing.cwd,
        disabled=bool(disabled),
    )
    servers = tuple(
        spec if item.name.lower() == existing.name.lower() else item
        for item in config.servers
    )
    saved = McpConfig(path=Path(target), servers=servers)
    written = save_mcp_config(saved, Path(target))
    return McpConfig(path=written, servers=servers), spec


def mcp_status_line(config: McpConfig | None = None) -> str:
    config = config if config is not None else load_mcp_config()
    enabled = config.enabled_servers
    if not enabled:
        searched = " or ".join(str(p) for p in config_search_paths())
        return (
            f"MCP: none configured (add servers in the localhost UI or mcp.json at {searched} "
            "to connect third-party tools without extra AI keys)"
        )
    names = ", ".join(s.name for s in enabled)
    origin = config.path or "mcp.json"
    return f"MCP: {names} (from {origin})"


def mcp_payload(config: McpConfig | None = None, manager=None) -> dict:
    """Structured MCP status for the UI and ``GET /api/mcp``."""
    config = config if config is not None else load_mcp_config()
    writable = writable_mcp_config_path()
    sessions = list(getattr(manager, "sessions", None) or [])
    connected = {session.name: session for session in sessions}
    failures = {
        str(name): str(err)
        for name, err in dict(getattr(manager, "failure_by_name", {}) or {}).items()
    }
    servers = []
    for spec in config.servers:
        row = spec.public_dict()
        session = connected.get(spec.name)
        row["connected"] = session is not None
        row["tools"] = (
            [str(tool.get("name") or "") for tool in session.tools if tool.get("name")]
            if session is not None
            else []
        )
        row["error"] = failures.get(spec.name, "")
        servers.append(row)
    return {
        "path": str(config.path) if config.path is not None else None,
        "writable_path": str(writable),
        "status": mcp_status_line(config),
        "servers": servers,
        "failures": list(getattr(manager, "failures", None) or []),
    }


def validate_mcp_name(name: object) -> str:
    ident = str(name or "").strip()
    if not ident:
        return _raise("Give the MCP server a short name (letters, numbers, dash, or dot).")
    if not _NAME_RE.match(ident):
        return _raise(
            "MCP server names must start with a letter or number and use only "
            "letters, numbers, dots, underscores, or dashes (max 64 characters)."
        )
    return ident


def validate_mcp_command(command: object) -> str:
    cmd = str(command or "").strip()
    if not cmd:
        return _raise("Give a command to launch the MCP server (for example: npx).")
    if "\x00" in cmd or "\n" in cmd or "\r" in cmd:
        return _raise("The MCP command cannot contain newlines.")
    if len(cmd) > _MAX_COMMAND:
        return _raise(f"The MCP command is too long (max {_MAX_COMMAND} characters).")
    return cmd


def parse_mcp_args(raw: object) -> tuple[str, ...]:
    if raw is None or raw == "":
        return ()
    items: list[str]
    if isinstance(raw, list):
        items = [str(item) for item in raw]
    elif isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise McpConfigError("Args must be a JSON array or a space-separated command.") from exc
            if not isinstance(parsed, list):
                raise McpConfigError("Args JSON must be an array of strings.")
            items = [str(item) for item in parsed]
        else:
            items = shlex.split(text)
    else:
        raise McpConfigError("Args must be a list of strings or a single command line.")
    if len(items) > _MAX_ARGS:
        raise McpConfigError(f"Too many MCP args (max {_MAX_ARGS}).")
    cleaned: list[str] = []
    for item in items:
        value = str(item)
        if "\x00" in value:
            raise McpConfigError("MCP args cannot contain null bytes.")
        if len(value) > _MAX_ARG:
            raise McpConfigError(f"An MCP arg is too long (max {_MAX_ARG} characters).")
        cleaned.append(value)
    return tuple(cleaned)


def parse_mcp_env(raw: object) -> dict[str, str]:
    if raw is None or raw == "" or raw == {}:
        return {}
    pairs: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            ident = str(key).strip()
            if not ident:
                continue
            pairs[ident] = str(value)
    elif isinstance(raw, str):
        for line in raw.splitlines():
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, value = text.split("=", 1)
            ident = key.strip()
            if ident:
                pairs[ident] = value.strip()
    else:
        raise McpConfigError("Env must be an object of KEY=value pairs or one pair per line.")
    if len(pairs) > _MAX_ENV:
        raise McpConfigError(f"Too many MCP env vars (max {_MAX_ENV}).")
    cleaned: dict[str, str] = {}
    for key, value in pairs.items():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            raise McpConfigError(f"Invalid env var name: {key}")
        if "\x00" in value or "\n" in value or "\r" in value:
            raise McpConfigError("MCP env values cannot contain newlines.")
        if len(value) > _MAX_ENV_VALUE:
            raise McpConfigError(f"An MCP env value is too long (max {_MAX_ENV_VALUE} characters).")
        cleaned[key] = value
    return cleaned


def _parse_cwd(raw: object, fallback: str | None) -> str | None:
    if raw is None:
        return fallback
    text = str(raw).strip()
    return text or None


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


def _read_raw_object(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _raise(message: str) -> str:
    raise McpConfigError(message)
