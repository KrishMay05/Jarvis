#!/usr/bin/env python3
"""Interactive entry point for Jarvis."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from src.assistant import build_orchestrator
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
    args = parser.parse_args()

    try:
        settings = get_llm_settings()
    except MissingAPIKeyError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    if args.status:
        print(describe_runtime(settings))
        return

    orchestrator = build_orchestrator(settings)
    if args.once:
        orchestrator.memory.append(f"User: {args.once}")
        print(orchestrator.handle_message(args.once))
        return

    print(f"Jarvis online · {settings.summary()}")
    print("Built-in tools need no extra keys. Type exit to leave.")
    orchestrator.run()


if __name__ == "__main__":
    main()
