"""A single-purpose agent that can call tools or reply to the user."""

from __future__ import annotations

from src.json_util import parse_llm_json
from src.llm import query_llm
from src.tools.base_tool import Tool


class Agent:
    def __init__(
        self,
        Name: str,
        Description: str,
        Tools: list[Tool],
        Model: str,
        memory_store=None,
    ):
        self.memory: list[str] = []
        self.name = Name
        self.description = Description
        self.tools = Tools
        self.model = Model
        self.max_memory = 10
        self.memory_store = memory_store

    def json_parser(self, input_string: str):
        return parse_llm_json(input_string)

    def process_input(self, user_input: str):
        self.memory = self.memory[-self.max_memory :]
        self.memory.append(f"User: {user_input}")

        if not self.tools:
            return self._reply_without_tools(user_input)

        context = "\n".join(self.memory)
        durable = self._durable_context()
        tool_descriptions = "\n".join(
            [f"- {tool.name()}: {tool.description()}" for tool in self.tools]
        )
        response_format = {"action": "", "args": ""}

        prompt = f"""Context:
        {context}
        {durable}

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

        try:
            response_dict = self.json_parser(response)
        except ValueError:
            return {"action": "respond_to_user", "args": response}

        if not isinstance(response_dict, dict):
            return {"action": "respond_to_user", "args": str(response_dict)}

        action = str(response_dict.get("action", "")).lower()
        args = response_dict.get("args", "")

        for tool in self.tools:
            if action in _tool_names(tool):
                return tool.use(args)

        return response_dict

    def _reply_without_tools(self, user_input: str):
        """General chat: skip the tool JSON protocol and answer in plain text."""
        context = "\n".join(self.memory)
        durable = self._durable_context()
        prompt = f"""You are {self.name}: {self.description}

Conversation:
{context}
{durable}

Reply helpfully as Jarvis — confident, concise, and useful.
Answer the user's latest message directly. Do not use JSON.
If the message is a greeting, greet them back and offer to help.
Use durable memories when they are relevant (name, city, preferences).
"""
        reply = query_llm(prompt, model=self.model).strip() or (
            "I'm here. How can I help?"
        )
        self.memory.append(f"Agent: {reply}")
        return {"action": "respond_to_user", "args": reply}

    def _durable_context(self) -> str:
        store = self.memory_store
        if store is None:
            return ""
        block = store.prompt_context()
        if not block:
            return ""
        return f"\nDurable memories from previous sessions:\n{block}\n"


def _tool_names(tool: Tool) -> set[str]:
    names = {tool.name().lower()}
    aliases = getattr(tool, "aliases", None)
    if callable(aliases):
        names.update(str(alias).lower() for alias in aliases() or ())
    return names
