import io
import json
import sys
from pathlib import Path

import pytest

from src.assistant import attach_mcp, build_orchestrator
from src.config import LLMSettings, describe_runtime
from src.mcp.config import (
    McpConfigError,
    load_mcp_config,
    mcp_payload,
    mcp_status_line,
    parse_mcp_args,
    parse_mcp_env,
    remove_mcp_server,
    set_mcp_server_disabled,
    upsert_mcp_server,
    writable_mcp_config_path,
)
from src.mcp.manager import McpManager
from src.mcp.protocol import MessageReader, encode_message
from src.mcp.session import McpError, McpSession
from src.tools.mcp_tool import format_call_result, normalize_mcp_arguments

FAKE_SERVER = Path(__file__).resolve().parent / "fake_mcp_server.py"


def _write_config(path: Path, extra_servers: dict | None = None) -> Path:
    servers = {
        "fake": {
            "command": sys.executable,
            "args": [str(FAKE_SERVER)],
        }
    }
    if extra_servers:
        servers.update(extra_servers)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


def test_encode_and_read_content_length_roundtrip():
    payload = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    raw = encode_message(payload)
    assert raw.startswith(b"Content-Length:")
    reader = MessageReader(io.BytesIO(raw), timeout=1)
    assert reader.read() == payload


def test_load_mcp_config_reads_claude_style_file(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fs": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                        "env": {"FOO": "bar"},
                    },
                    "off": {"command": "true", "disabled": True},
                    "bad": {"args": ["no-command"]},
                }
            }
        ),
        encoding="utf-8",
    )
    config = load_mcp_config(config_path)
    assert config.path == config_path
    assert [s.name for s in config.enabled_servers] == ["fs"]
    spec = config.enabled_servers[0]
    assert spec.command == "npx"
    assert spec.args[-1] == "/tmp"
    assert spec.env["FOO"] == "bar"


def test_load_mcp_config_missing_file(tmp_path):
    config = load_mcp_config(tmp_path / "nope.json")
    assert config.servers == ()


def test_mcp_status_line_without_servers(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_MCP_CONFIG", str(tmp_path / "missing.json"))
    text = mcp_status_line()
    assert text.startswith("MCP: none configured")


def test_mcp_status_line_lists_servers(tmp_path):
    path = _write_config(tmp_path / "mcp.json")
    text = mcp_status_line(load_mcp_config(path))
    assert "fake" in text
    assert str(path) in text


def test_mcp_session_lists_and_calls_fake_server():
    from src.mcp.config import McpServerSpec

    session = McpSession(
        McpServerSpec(name="fake", command=sys.executable, args=(str(FAKE_SERVER),))
    )
    session.start()
    try:
        names = {tool["name"] for tool in session.tools}
        assert names == {"echo", "add"}
        result = session.call_tool("echo", {"text": "hello-mcp"})
        assert format_call_result(result) == "hello-mcp"
        added = session.call_tool("add", {"a": 2, "b": 3})
        assert format_call_result(added) == "5"
    finally:
        session.close()


def test_mcp_session_missing_command_raises():
    from src.mcp.config import McpServerSpec

    session = McpSession(
        McpServerSpec(name="gone", command="jarvis-mcp-command-does-not-exist")
    )
    with pytest.raises(McpError, match="Failed to start"):
        session.start()


def test_mcp_manager_skips_broken_servers_and_keeps_healthy_ones(tmp_path):
    path = _write_config(
        tmp_path / "mcp.json",
        extra_servers={
            "broken": {"command": "jarvis-mcp-command-does-not-exist"},
        },
    )
    manager = McpManager(load_mcp_config(path)).start()
    try:
        assert [session.name for session in manager.sessions] == ["fake"]
        assert manager.failures
        names = {tool.name() for tool in manager.tools}
        assert "echo" in names
        assert "add" in names
        echo = next(tool for tool in manager.tools if tool.name() == "echo")
        assert echo.use("ping") == "ping"
        assert "echo" in echo.aliases()
        assert "fake/echo" in echo.aliases()
    finally:
        manager.close()


def test_build_orchestrator_adds_mcp_agent_when_configured(monkeypatch, tmp_path):
    path = _write_config(tmp_path / "mcp.json")
    monkeypatch.setenv("JARVIS_MCP_CONFIG", str(path))
    settings = LLMSettings(provider="openai", api_key="test", model="gpt-4o-mini")
    orchestrator = build_orchestrator(settings)
    try:
        names = {agent.name for agent in orchestrator.agents}
        assert "MCP Agent" in names
        mcp_agent = next(agent for agent in orchestrator.agents if agent.name == "MCP Agent")
        assert {tool.name() for tool in mcp_agent.tools} == {"echo", "add"}
    finally:
        orchestrator.close()


def test_describe_runtime_mentions_mcp(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.delenv("JARVIS_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("JARVIS_MCP_CONFIG", str(tmp_path / "missing.json"))
    text = describe_runtime()
    assert "MCP:" in text


def test_normalize_mcp_arguments_uses_required_field():
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    assert normalize_mcp_arguments("hello", schema) == {"text": "hello"}
    assert normalize_mcp_arguments({"arguments": {"text": "x"}}, schema) == {"text": "x"}
    assert normalize_mcp_arguments('{"text": "y"}', schema) == {"text": "y"}


def test_format_call_result_marks_errors():
    text = format_call_result(
        {"content": [{"type": "text", "text": "nope"}], "isError": True}
    )
    assert text.startswith("MCP tool error:")
    assert "nope" in text


def test_writable_path_uses_explicit_env(tmp_path, monkeypatch):
    path = tmp_path / "custom-mcp.json"
    monkeypatch.setenv("JARVIS_MCP_CONFIG", str(path))
    assert writable_mcp_config_path() == path


def test_upsert_save_remove_and_disable_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    monkeypatch.setenv("JARVIS_MCP_CONFIG", str(path))
    config, spec, created = upsert_mcp_server(
        "filesystem",
        "npx",
        args="-y @modelcontextprotocol/server-filesystem /tmp",
        env={"TOKEN": "secret-value"},
    )
    assert created is True
    assert spec.name == "filesystem"
    assert spec.args[-1] == "/tmp"
    assert spec.env["TOKEN"] == "secret-value"
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["mcpServers"]["filesystem"]["env"]["TOKEN"] == "secret-value"
    public = spec.public_dict()
    assert public["env_keys"] == ["TOKEN"]
    assert "secret-value" not in json.dumps(public)
    payload = mcp_payload(config)
    assert payload["servers"][0]["env_keys"] == ["TOKEN"]
    assert "secret-value" not in json.dumps(payload)

    config, spec, created = upsert_mcp_server(
        "filesystem",
        "npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/home"],
    )
    assert created is False
    assert spec.args[-1] == "/home"
    assert spec.env["TOKEN"] == "secret-value"

    config, spec = set_mcp_server_disabled("filesystem", True)
    assert spec is not None and spec.disabled is True
    assert load_mcp_config(path).enabled_servers == ()

    config, removed = remove_mcp_server("filesystem")
    assert removed is not None
    assert config.servers == ()
    assert load_mcp_config(path).servers == ()


def test_parse_mcp_args_and_env():
    assert parse_mcp_args('-y server "/tmp/My Files"') == ("-y", "server", "/tmp/My Files")
    assert parse_mcp_args('["-y", "server"]') == ("-y", "server")
    assert parse_mcp_env("FOO=bar\n# skip\nBAZ=qux") == {"FOO": "bar", "BAZ": "qux"}
    try:
        parse_mcp_env({"bad name": "x"})
        assert False, "expected invalid env name to fail"
    except McpConfigError:
        pass


def test_attach_mcp_reloads_live_orchestrator(monkeypatch, tmp_path):
    path = tmp_path / "mcp.json"
    monkeypatch.setenv("JARVIS_MCP_CONFIG", str(path))
    settings = LLMSettings(provider="openai", api_key="test", model="gpt-4o-mini")
    orchestrator = build_orchestrator(settings)
    try:
        names = {agent.name for agent in orchestrator.agents}
        assert "MCP Agent" not in names
        upsert_mcp_server(
            "fake",
            sys.executable,
            args=[str(FAKE_SERVER)],
        )
        attach_mcp(orchestrator)
        names = {agent.name for agent in orchestrator.agents}
        assert "MCP Agent" in names
        mcp_agent = next(agent for agent in orchestrator.agents if agent.name == "MCP Agent")
        assert {tool.name() for tool in mcp_agent.tools} == {"echo", "add"}
    finally:
        orchestrator.close()
