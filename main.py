#!/usr/bin/env python3
"""Interactive entry point for Jarvis."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from src.assistant import build_orchestrator
from src.auth.oauth import connect_google, disconnect_google
from src.auth.store import AuthStore
from src.automation.runner import format_due_report
from src.automation.store import AutomationStore
from src.config import MissingAPIKeyError, describe_runtime, get_llm_settings
from src.tools.computer_tool import ComputerTool


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
    parser.add_argument(
        "--browse",
        metavar="URL",
        help="Open a public URL and print readable text (no API key needed)",
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Show connected OAuth accounts and exit (no API key needed)",
    )
    parser.add_argument(
        "--connect",
        metavar="PROVIDER",
        nargs="?",
        const="google",
        help="Connect an account via OAuth (default: google). No AI key needed",
    )
    parser.add_argument(
        "--disconnect",
        metavar="PROVIDER",
        nargs="?",
        const="google",
        help="Forget a stored OAuth login (default: google)",
    )
    args = parser.parse_args()

    if args.browse and not args.run_due and not args.once and not args.status:
        print(ComputerTool().use(args.browse))
        return

    if args.automations and not args.run_due and not args.once and not args.status:
        print(AutomationStore().format_list())
        return

    if args.auth and not args.run_due and not args.once and not args.status:
        print(AuthStore().format_status())
        return

    if args.disconnect and not args.run_due and not args.once and not args.status:
        _run_disconnect(args.disconnect)
        return

    if args.connect and not args.run_due and not args.once and not args.status:
        _run_connect(args.connect)
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
            "Memory and automations persist locally. Computer use can open "
            "public web pages. Mail and calendar use Google OAuth "
            "(--connect google). MCP servers come from mcp.json."
        )
        print("Type exit to leave.")
        orchestrator.run()
    finally:
        if orchestrator is not None:
            orchestrator.close()


def _run_connect(provider: str) -> None:
    name = (provider or "google").strip().lower()
    if name != "google":
        print(
            f"Unknown account '{provider}'. Supported: google",
            file=sys.stderr,
        )
        sys.exit(1)
    print(connect_google())


def _run_disconnect(provider: str) -> None:
    name = (provider or "google").strip().lower()
    if name != "google":
        print(
            f"Unknown account '{provider}'. Supported: google",
            file=sys.stderr,
        )
        sys.exit(1)
    print(disconnect_google())


if __name__ == "__main__":
    main()
