"""Colored console logging for orchestrator steps."""

from termcolor import colored


def log_message(message: str, level: str) -> None:
    level_key = level.upper()
    if level_key == "REASON":
        print(colored("REASON: " + message, "blue"))
    elif level_key == "ACTION":
        print(colored("ACTION: " + message, "yellow"))
    elif level_key == "ERROR":
        print(colored("ERROR: " + message, "red"))
    elif level_key == "RESPONSE":
        print(colored("RESPONSE: " + message, "green"))
    else:
        print(message)
