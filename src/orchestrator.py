"""Routes a user request to the right specialist agent."""

from __future__ import annotations

from src.agent import Agent
from src.json_util import parse_llm_json
from src.llm import query_llm
from src.logger import log_message

_EMPTY_FOLLOWUPS = frozenset(
    {"", "none", "null", "n/a", "na", "respond_to_user", "-"}
)


class AgentOrchestrator:
    def __init__(
        self,
        agents: list[Agent],
        max_steps: int = 5,
        closables: list | None = None,
        memory_store=None,
    ):
        self.agents = agents
        self.memory: list[str] = []
        self.max_memory = 10
        self.max_steps = max_steps
        self._closables = list(closables or [])
        self.memory_store = memory_store

    def json_parser(self, input_string: str):
        return parse_llm_json(input_string)

    def orchestrate_task(self, user_input: str):
        self.memory = self.memory[-self.max_memory :]
        context = "\n".join(self.memory)
        durable = ""
        if self.memory_store is not None:
            durable = self.memory_store.prompt_context()
        response_format = {"action": "", "input": "", "next_action": ""}

        agent_catalog = ", ".join(
            [f"- {agent.name}: {agent.description}" for agent in self.agents]
        )
        prompt = f"""
                Use the context from memory to plan next steps.
                Context:
                {context}

                Durable memories (persist across sessions; apply them when relevant):
                {durable or "(none yet)"}

                You are an expert intent classifier.
                Use the context and the user's input to select the appropriate agent.
                Rewrite the input so that the agent can execute the task efficiently.

                Here are the available agents and their descriptions:
                {agent_catalog}

                User Input:
                {user_input}

                ###Guidelines###
                - Prefer a specialist (weather, time, research, memory, MCP) when the user needs that capability.
                - Use the Memory Agent to remember, forget, or recall lasting personal facts. Also use it when the user states a new lasting fact (name, home city, units, preferences).
                - When the user omits a detail that a durable memory covers (for example home city), rewrite the specialist input with that detail.
                - Use the Chat Agent for greetings, conversation, writing, math, coding help, advice, and anything that does not need a specialist.
                - Compound requests may need several agents in a loop. Read the context for results already gathered.
                - After specialist results are in context, either pick the next missing specialist or respond_to_user with a complete answer that uses those results.
                - Do not select an agent whose result is already in context unless you still need a different query from it.
                - If there are no further agents to run, make the action "respond_to_user" with a polished final answer as input.
                - Return the agent name in the form of {response_format}
                - Always return valid JSON like {response_format} and nothing else.
                """

        raw = query_llm(prompt)
        try:
            llm_response = self.json_parser(raw)
        except ValueError:
            return {
                "action": "respond_to_user",
                "input": raw.strip() or "I had trouble planning that request.",
            }

        self.memory.append(f"Orchestrator: {llm_response}")

        if not isinstance(llm_response, dict):
            return {"action": "respond_to_user", "input": str(llm_response)}

        action = llm_response.get("action", "")
        action_name = str(action).strip().lower()
        rewritten_input = llm_response.get("input", user_input)

        if action_name == "respond_to_user":
            return llm_response

        for agent in self.agents:
            if agent.name.lower() == action_name:
                agent_response = agent.process_input(rewritten_input)
                text, direct_reply = _result_text_and_direct(agent_response)
                self.memory.append(f"{agent.name} result: {text}")
                if direct_reply and not _has_followup(llm_response):
                    return {"action": "respond_to_user", "input": text}
                return {
                    "action": "observation",
                    "agent": agent.name,
                    "input": text,
                }

        return {
            "action": "respond_to_user",
            "input": f"No agent named '{action}' is available.",
        }

    def handle_message(self, user_input: str) -> str:
        """Run the orchestrator until it produces a user-facing reply.

        Tool/agent observations continue the loop so compound requests
        (weather + time, research then chat) can finish instead of
        returning the first raw tool string.
        """
        pending = user_input
        observations: list[str] = []
        reply = "I could not finish that request in the allowed number of steps."
        for _ in range(self.max_steps):
            response = self.orchestrate_task(pending)
            if not isinstance(response, dict):
                reply = str(response)
                break

            action = str(response.get("action") or "").strip().lower()
            if action == "respond_to_user":
                reply = str(response.get("input") or response.get("args") or "")
                break
            if action == "observation":
                snippet = str(response.get("input") or "")
                label = response.get("agent") or "Agent"
                observations.append(f"{label}: {snippet}")
                pending = (
                    f"Original user request: {user_input}\n"
                    "Agent results so far:\n"
                    + "\n".join(observations)
                    + "\nIf another agent is needed, select it. "
                    "If the request is complete, respond_to_user with a helpful "
                    "final answer that uses the agent results."
                )
                continue
            reply = str(response.get("input") or response)
            break
        else:
            reply = "I could not finish that request in the allowed number of steps."

        if self.memory_store is not None:
            self.memory_store.record_exchange(user_input, reply)
        return reply

    def run(self) -> None:
        print("Jarvis: At your service. How can I help?")
        user_input = input("You: ")
        self.memory.append(f"User: {user_input}")

        while True:
            if user_input.lower() in ["exit", "bye", "close"]:
                print("See you later!")
                break

            response = self.handle_message(user_input)
            log_message(f"Response from Agent: {response}", "RESPONSE")
            user_input = input("You: ")
            self.memory.append(f"User: {user_input}")

    def close(self) -> None:
        """Release long-lived resources such as MCP server processes."""
        while self._closables:
            resource = self._closables.pop()
            closer = getattr(resource, "close", None)
            if not callable(closer):
                continue
            try:
                closer()
            except Exception:
                pass


def _has_followup(decision: dict) -> bool:
    nxt = str(decision.get("next_action") or "").strip().lower()
    return nxt not in _EMPTY_FOLLOWUPS


def _result_text_and_direct(result) -> tuple[str, bool]:
    """Normalize an agent return value to (text, is_direct_user_reply)."""
    if isinstance(result, dict):
        action = str(result.get("action") or "").strip().lower()
        text = result.get("args")
        if text in (None, ""):
            text = result.get("input")
        if text in (None, ""):
            text = str(result)
        return str(text), action == "respond_to_user"
    return str(result), False
