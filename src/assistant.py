"""Assemble the default Jarvis agent roster."""

from __future__ import annotations

from src.agent import Agent
from src.auth.store import AuthStore
from src.automation.store import AutomationStore
from src.config import LLMSettings, get_llm_settings
from src.mcp.manager import McpManager, start_mcp_manager
from src.memory.store import MemoryStore
from src.orchestrator import AgentOrchestrator
from src.tools.automation_tool import AutomationTool
from src.tools.calendar_tool import CalendarTool
from src.tools.computer_tool import ComputerTool
from src.tools.mail_tool import MailTool
from src.tools.memory_tool import MemoryTool
from src.tools.research_tool import ResearchTool
from src.tools.time_tool import TimeTool
from src.tools.weather_tool import WeatherTool


def build_orchestrator(settings: LLMSettings | None = None) -> AgentOrchestrator:
    """Create the stock personal-assistant lineup for the configured LLM."""
    settings = settings or get_llm_settings()
    model = settings.model
    memory = MemoryStore()
    automations = AutomationStore()
    auth = AuthStore()

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
    automation_agent = Agent(
        Name="Automation Agent",
        Description=(
            "Schedules, lists, pauses, and cancels local reminders and "
            "recurring Jarvis prompts (research, weather checks). Use when "
            "the user says remind me, every morning, daily, or mentions "
            "automations. Local file only — no extra API key or cron account."
        ),
        Tools=[AutomationTool(automations)],
        Model=model,
        memory_store=memory,
    )
    computer_agent = Agent(
        Name="Computer Agent",
        Description=(
            "Computer use: opens public web pages, reads the visible text, "
            "lists links, and follows a link from the last page. Use when "
            "the user pastes a URL, says open/browse/go to a site, asks "
            "what is on a page, or wants to click a link. Local HTTP only "
            "— no extra API key. Not for encyclopedic research without a URL."
        ),
        Tools=[ComputerTool()],
        Model=model,
        memory_store=memory,
    )
    mail_agent = Agent(
        Name="Mail Agent",
        Description=(
            "Reads Gmail (inbox, unread, search) after Google is connected "
            "with `python main.py --connect google` or Connect Google in the "
            "localhost web UI. Use when the user asks about email, inbox, "
            "unread mail, or a message from someone. OAuth login — not a "
            "second AI key. If Google is not connected, tell them to connect."
        ),
        Tools=[MailTool(auth)],
        Model=model,
        memory_store=memory,
    )
    calendar_agent = Agent(
        Name="Calendar Agent",
        Description=(
            "Reads upcoming Google Calendar events after Google is connected "
            "with `python main.py --connect google` or Connect Google in the "
            "localhost web UI. Use when the user asks what's on the calendar, "
            "upcoming meetings, or today's agenda. OAuth login — not a second "
            "AI key. If Google is not connected, tell them to connect."
        ),
        Tools=[CalendarTool(auth)],
        Model=model,
        memory_store=memory,
    )
    chat_agent = Agent(
        Name="Chat Agent",
        Description=(
            "General conversation, writing, math, coding help, brainstorming, "
            "and questions that do not need weather, time, research, memory, "
            "automations, computer use, mail, calendar, or MCP tools. Default for greetings "
            "and open-ended chat. Uses the same AI key — no extra accounts."
        ),
        Tools=[],
        Model=model,
        memory_store=memory,
    )
    agents = [
        weather_agent,
        time_agent,
        research_agent,
        memory_agent,
        automation_agent,
        computer_agent,
        mail_agent,
        calendar_agent,
        chat_agent,
    ]
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

    return AgentOrchestrator(
        agents,
        closables=closables,
        memory_store=memory,
        automation_store=automations,
        auth_store=auth,
    )
