"""Routes a user request to the right specialist agent."""

from __future__ import annotations

from src.agent import Agent
from src.json_util import parse_llm_json
from src.llm import query_llm
from src.logger import log_message


class AgentOrchestrator:
    def __init__(self, agents: list[Agent], max_steps: int = 5):
        self.agents = agents
        self.memory: list[str] = []
        self.max_memory = 10
        self.max_steps = max_steps

    def json_parser(self, input_string: str):
        return parse_llm_json(input_string)

    def orchestrate_task(self, user_input: str):
        self.memory = self.memory[-self.max_memory :]
        context = "\n".join(self.memory)
        response_format = {"action": "", "input": "", "next_action": ""}

        agent_catalog = ", ".join(
            [f"- {agent.name}: {agent.description}" for agent in self.agents]
        )
        prompt = f"""
                Use the context from memory to plan next steps.
                Context:
                {context}

                You are an expert intent classifier.
                You need will use the context provided and the user's input to classify the intent select the appropriate agent.
                You will rewrite the input for the agent so that the agent can efficiently execute the task.

                Here are the available agents and their descriptions:
                {agent_catalog}

                User Input:
                {user_input}

                ###Guidelines###
                - Sometimes you might have to use multiple agents to solve user's input. You have to do that in a loop.
                - The original user input could have multiple tasks, you will use the context to understand the previous actions taken and the next steps you should take.
                - Read the context, take your time to understand, see if there were many tasks and if you executed them all
                - If there are no actions to be taken, then make the action "respond_to_user" with your final thoughts combining all previous responses as input.
                - Respond with "respond_to_user" only when there are no agents to select from or there is no next_action
                - You will return the agent name in the form of {response_format}
                - Always return valid JSON like {response_format} and nothing else.
                """

        llm_response = self.json_parser(query_llm(prompt))
        self.memory.append(f"Orchestrator: {llm_response}")

        if not isinstance(llm_response, dict):
            return {"action": "respond_to_user", "input": str(llm_response)}

        action = llm_response.get("action", "")
        rewritten_input = llm_response.get("input", user_input)

        if action == "respond_to_user":
            return llm_response

        for agent in self.agents:
            if agent.name == action:
                agent_response = agent.process_input(rewritten_input)
                self.memory.append(f"Agent Response for Task: {agent_response}")
                return agent_response

        return {
            "action": "respond_to_user",
            "input": f"No agent named '{action}' is available.",
        }

    def handle_message(self, user_input: str) -> str:
        """Run the orchestrator until it produces a user-facing reply."""
        pending = user_input
        for _ in range(self.max_steps):
            response = self.orchestrate_task(pending)
            if isinstance(response, dict) and response.get("action") == "respond_to_user":
                return str(response.get("input") or response.get("args") or "")
            if isinstance(response, str):
                return response
            pending = str(response)
        return "I could not finish that request in the allowed number of steps."

    def run(self) -> None:
        print("LLM Agent: Hello! How can I assist you today?")
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
