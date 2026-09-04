"""A single-purpose agent that can call tools or reply to the user."""

from __future__ import annotations

from src.json_util import parse_llm_json
from src.llm import query_llm
from src.tools.base_tool import Tool


class Agent:
    def __init__(self, Name: str, Description: str, Tools: list[Tool], Model: str):
        self.memory: list[str] = []
        self.name = Name
        self.description = Description
        self.tools = Tools
        self.model = Model
        self.max_memory = 10

    def json_parser(self, input_string: str):
        return parse_llm_json(input_string)

    def process_input(self, user_input: str):
        self.memory = self.memory[-self.max_memory :]
        self.memory.append(f"User: {user_input}")

        context = "\n".join(self.memory)
        tool_descriptions = "\n".join(
            [f"- {tool.name()}: {tool.description()}" for tool in self.tools]
        )
        response_format = {"action": "", "args": ""}

        prompt = f"""Context:
        {context}

        Available tools:
        {tool_descriptions}

        Based on the user's input and context, decide if you should use a tool or respond directly.
        If you identify an action, respond with the tool name and the arguments for the tool.
        If you decide to respond directly to the user then make the action "respond_to_user" with args as your response in the following format.

        Response Format:
        {response_format}

        Always return valid JSON and nothing else.
        """

        response = query_llm(prompt, model=self.model)
        self.memory.append(f"Agent: {response}")

        response_dict = self.json_parser(response)
        if not isinstance(response_dict, dict):
            return {"action": "respond_to_user", "args": str(response_dict)}

        action = str(response_dict.get("action", "")).lower()
        args = response_dict.get("args", "")

        for tool in self.tools:
            if action in _tool_names(tool):
                return tool.use(args)

        return response_dict


def _tool_names(tool: Tool) -> set[str]:
    names = {tool.name().lower()}
    aliases = getattr(tool, "aliases", None)
    if callable(aliases):
        names.update(str(alias).lower() for alias in aliases() or ())
    return names
