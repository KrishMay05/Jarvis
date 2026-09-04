"""Local MCP (Model Context Protocol) client — no extra AI API key required.

Drop a Claude-style ``mcp.json`` next to the project or at ``~/.jarvis/mcp.json``
and Jarvis will spawn those stdio servers and expose their tools as an agent.
"""

from src.mcp.config import McpConfig, McpServerSpec, load_mcp_config, mcp_status_line

__all__ = [
    "McpConfig",
    "McpServerSpec",
    "load_mcp_config",
    "mcp_status_line",
]
