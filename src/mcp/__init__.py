"""Local MCP (Model Context Protocol) client — no extra AI API key required.

Drop a Claude-style ``mcp.json`` next to the project or at ``~/.jarvis/mcp.json``
and Jarvis will spawn those stdio servers and expose their tools as an agent.
"""

from src.mcp.config import (
    McpConfig,
    McpConfigError,
    McpServerSpec,
    load_mcp_config,
    mcp_payload,
    mcp_status_line,
    remove_mcp_server,
    save_mcp_config,
    set_mcp_server_disabled,
    upsert_mcp_server,
    writable_mcp_config_path,
)

__all__ = [
    "McpConfig",
    "McpConfigError",
    "McpServerSpec",
    "load_mcp_config",
    "mcp_payload",
    "mcp_status_line",
    "remove_mcp_server",
    "save_mcp_config",
    "set_mcp_server_disabled",
    "upsert_mcp_server",
    "writable_mcp_config_path",
]
