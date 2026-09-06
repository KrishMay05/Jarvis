#!/usr/bin/env python3
"""Interactive entry point for Jarvis."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from src.assistant import build_orchestrator
from src.automation.runner import format_due_report
from src.automation.store import AutomationStore
from src.config import MissingAPIKeyError, describe_runtime, get_llm_settings


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Jarvis personal assistant")
    parser.add_argument(
        "--once",
        metavar="PROMPT",
        help="Run a single prompt and exit instead of starting the REPL",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print the detected LLM provider and built-in tools, then exit",
    )
    parser.add_argument(
        "--automations",
        action="store_true",
        help="List scheduled local automations and exit (no API key needed)",
    )
    parser.add_argument(
        "--run-due",
        action="store_true",
        help="Fire due reminders and run-jobs, then exit (for system cron)",
    )
    args = parser.parse_args()

    if args.automations and not args.run_due and not args.once and not args.status:
        print(AutomationStore().format_list())
        return

    try:
        settings = get_llm_settings()
    except MissingAPIKeyError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    if args.status:
        print(describe_runtime(settings))
        return

    orchestrator = None
    try:
        orchestrator = build_orchestrator(settings)
        if args.run_due:
            print(format_due_report(orchestrator.drain_due_automations()))
            return
        if args.once:
            for line in orchestrator.drain_due_automations():
                print(line)
            orchestrator.memory.append(f"User: {args.once}")
            print(orchestrator.handle_message(args.once))
            return

        print(f"Jarvis online · {settings.summary()}")
        print(
            "Built-in tools need no extra keys. Chat uses the same LLM. "
            "Memory and automations persist locally. MCP servers come from mcp.json."
        )
        print("Type exit to leave.")
        orchestrator.run()
    finally:
        if orchestrator is not None:
            orchestrator.close()


if __name__ == "__main__":
    main()
