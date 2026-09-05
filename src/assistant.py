"""Assemble the default Jarvis agent roster."""

from __future__ import annotations

from src.agent import Agent
from src.config import LLMSettings, get_llm_settings
from src.mcp.manager import McpManager, start_mcp_manager
from src.memory.store import MemoryStore
from src.orchestrator import AgentOrchestrator
from src.tools.memory_tool import MemoryTool
from src.tools.research_tool import ResearchTool
from src.tools.time_tool import TimeTool
from src.tools.weather_tool import WeatherTool


def build_orchestrator(settings: LLMSettings | None = None) -> AgentOrchestrator:
    """Create the stock personal-assistant lineup for the configured LLM."""
    settings = settings or get_llm_settings()
    model = settings.model
    memory = MemoryStore()

    weather_agent = Agent(
        Name="Weather Agent",
        Description="Provides weather information for a given location",
        Tools=[WeatherTool()],
        Model=model,
        memory_store=memory,
    )
    time_agent = Agent(
        Name="Time Agent",
        Description="Provides the current time for a given city",
        Tools=[TimeTool()],
        Model=model,
        memory_store=memory,
    )
    research_agent = Agent(
        Name="Research Agent",
        Description=(
            "Looks up facts, encyclopedic background, and public-web summaries. "
            "Use for questions that need research rather than weather or time."
        ),
        Tools=[ResearchTool()],
        Model=model,
        memory_store=memory,
    )
    memory_agent = Agent(
        Name="Memory Agent",
        Description=(
            "Remembers, recalls, and forgets personal facts and preferences "
            "across sessions (name, home city, units, habits). Use when the "
            "user says remember/forget, shares a lasting fact, or asks what "
            "you know about them. Local file only — no extra API key."
        ),
        Tools=[MemoryTool(memory)],
        Model=model,
        memory_store=memory,
    )
    chat_agent = Agent(
        Name="Chat Agent",
        Description=(
            "General conversation, writing, math, coding help, brainstorming, "
            "and questions that do not need weather, time, research, memory, "
            "or MCP tools. Default for greetings and open-ended chat. Uses "
            "the same AI key — no extra accounts."
        ),
        Tools=[],
        Model=model,
        memory_store=memory,
    )
    agents = [weather_agent, time_agent, research_agent, memory_agent, chat_agent]
    closables: list[McpManager] = []

    mcp = start_mcp_manager()
    if mcp.tools:
        agents.append(
            Agent(
                Name="MCP Agent",
                Description=mcp.agent_description(),
                Tools=mcp.tools,
                Model=model,
                memory_store=memory,
            )
        )
        closables.append(mcp)
    else:
        mcp.close()

    return AgentOrchestrator(agents, closables=closables, memory_store=memory)
