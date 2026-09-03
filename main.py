#!/usr/bin/env python3
"""Interactive entry point for Jarvis."""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from src.agent import Agent
from src.orchestrator import AgentOrchestrator
from src.tools.time_tool import TimeTool
from src.tools.weather_tool import WeatherTool


def build_orchestrator() -> AgentOrchestrator:
    weather_agent = Agent(
        Name="Weather Agent",
        Description="Provides weather information for a given location",
        Tools=[WeatherTool()],
        Model="gemini-2.0-flash",
    )
    time_agent = Agent(
        Name="Time Agent",
        Description="Provides the current time for a given city",
        Tools=[TimeTool()],
        Model="gemini-2.0-flash",
    )
    return AgentOrchestrator([weather_agent, time_agent])


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Jarvis multi-agent assistant")
    parser.add_argument(
        "--once",
        metavar="PROMPT",
        help="Run a single prompt and exit instead of starting the REPL",
    )
    args = parser.parse_args()

    orchestrator = build_orchestrator()
    if args.once:
        orchestrator.memory.append(f"User: {args.once}")
        print(orchestrator.handle_message(args.once))
        return

    orchestrator.run()


if __name__ == "__main__":
    main()
