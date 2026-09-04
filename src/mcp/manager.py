"""Start configured MCP servers and expose their tools to Jarvis agents."""

from __future__ import annotations

from src.logger import log_message
from src.mcp.config import McpConfig, load_mcp_config
from src.mcp.session import McpError, McpSession
from src.tools.base_tool import Tool
from src.tools.mcp_tool import McpTool


class McpManager:
    def __init__(self, config: McpConfig | None = None):
        self.config = config if config is not None else load_mcp_config()
        self.sessions: list[McpSession] = []
        self.tools: list[Tool] = []
        self.failures: list[str] = []

    def start(self) -> "McpManager":
        for spec in self.config.enabled_servers:
            session = McpSession(spec)
            try:
                session.start()
            except McpError as exc:
                self.failures.append(str(exc))
                log_message(str(exc), "ERROR")
                continue
            self.sessions.append(session)
        self.tools = _unique_tools(self.sessions)
        return self

    def agent_description(self) -> str:
        server_names = ", ".join(session.name for session in self.sessions) or "none"
        tool_names = [tool.name() for tool in self.tools]
        preview = ", ".join(tool_names[:12])
        extra = f" (+{len(tool_names) - 12} more)" if len(tool_names) > 12 else ""
        tools_text = preview + extra if tool_names else "none listed yet"
        return (
            "Runs tools from connected MCP servers "
            f"({server_names}). Use this agent for files, browsers, GitHub, "
            "databases, or any other third-party capability exposed over MCP. "
            f"Available tools: {tools_text}. "
            "No extra AI API key is required; servers are local processes "
            "declared in mcp.json."
        )

    def close(self) -> None:
        while self.sessions:
            session = self.sessions.pop()
            try:
                session.close()
            except Exception:
                pass
        self.tools = []


def start_mcp_manager(config: McpConfig | None = None) -> McpManager:
    return McpManager(config).start()


def _unique_tools(sessions: list[McpSession]) -> list[Tool]:
    declared: list[tuple[McpSession, dict]] = []
    for session in sessions:
        for tool_def in session.tools:
            declared.append((session, tool_def))

    counts: dict[str, int] = {}
    for _, tool_def in declared:
        raw = str(tool_def["name"])
        counts[raw] = counts.get(raw, 0) + 1

    tools: list[Tool] = []
    for session, tool_def in declared:
        raw = str(tool_def["name"])
        public = raw if counts[raw] == 1 else f"{session.name}__{raw}"
        aliases = (raw, f"{session.name}__{raw}", f"{session.name}/{raw}")
        tools.append(
            McpTool(
                session=session,
                tool_def=tool_def,
                public_name=public,
                aliases=aliases,
            )
        )
    return tools
