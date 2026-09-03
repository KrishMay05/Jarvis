"""Assemble the default Jarvis agent roster."""

from __future__ import annotations

from src.agent import Agent
from src.config import LLMSettings, get_llm_settings
from src.orchestrator import AgentOrchestrator
from src.tools.research_tool import ResearchTool
from src.tools.time_tool import TimeTool
from src.tools.weather_tool import WeatherTool


def build_orchestrator(settings: LLMSettings | None = None) -> AgentOrchestrator:
    """Create the stock personal-assistant lineup for the configured LLM."""
    settings = settings or get_llm_settings()
    model = settings.model

    weather_agent = Agent(
        Name="Weather Agent",
        Description="Provides weather information for a given location",
        Tools=[WeatherTool()],
        Model=model,
    )
    time_agent = Agent(
        Name="Time Agent",
        Description="Provides the current time for a given city",
        Tools=[TimeTool()],
        Model=model,
    )
    research_agent = Agent(
        Name="Research Agent",
        Description=(
            "Looks up facts, encyclopedic background, and public-web summaries. "
            "Use for questions that need research rather than weather or time."
        ),
        Tools=[ResearchTool()],
        Model=model,
    )
    return AgentOrchestrator([weather_agent, time_agent, research_agent])
