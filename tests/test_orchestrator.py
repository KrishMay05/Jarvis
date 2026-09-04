from src.agent import Agent
from src.orchestrator import AgentOrchestrator
from src.tools.base_tool import Tool


class FakeTool(Tool):
    def name(self) -> str:
        return "echo"

    def description(self) -> str:
        return "Echo the arguments"

    def use(self, args) -> str:
        return f"echoed:{args}"


def test_agent_uses_matching_tool(monkeypatch):
    agent = Agent(
        Name="Echo Agent",
        Description="Repeats input",
        Tools=[FakeTool()],
        Model="gemini-2.0-flash",
    )

    monkeypatch.setattr(
        "src.agent.query_llm",
        lambda prompt, model="gemini-2.0-flash": '{"action": "echo", "args": "hi"}',
    )

    assert agent.process_input("say hi") == "echoed:hi"


def test_orchestrator_routes_to_named_agent(monkeypatch):
    agent = Agent(
        Name="Echo Agent",
        Description="Repeats input",
        Tools=[FakeTool()],
        Model="gemini-2.0-flash",
    )
    orchestrator = AgentOrchestrator([agent])

    monkeypatch.setattr(
        "src.orchestrator.query_llm",
        lambda prompt, model="gemini-2.0-flash": '{"action": "Echo Agent", "input": "hi", "next_action": ""}',
    )
    monkeypatch.setattr(
        "src.agent.query_llm",
        lambda prompt, model="gemini-2.0-flash": '{"action": "echo", "args": "hi"}',
    )

    assert orchestrator.handle_message("please echo hi") == "echoed:hi"


def test_orchestrator_matches_agent_name_case_insensitively(monkeypatch):
    agent = Agent(
        Name="MCP Agent",
        Description="Runs MCP tools",
        Tools=[FakeTool()],
        Model="gemini-2.0-flash",
    )
    orchestrator = AgentOrchestrator([agent])
    monkeypatch.setattr(
        "src.orchestrator.query_llm",
        lambda prompt, model="gemini-2.0-flash": '{"action": "mcp agent", "input": "hi", "next_action": ""}',
    )
    monkeypatch.setattr(
        "src.agent.query_llm",
        lambda prompt, model="gemini-2.0-flash": '{"action": "echo", "args": "hi"}',
    )
    assert orchestrator.handle_message("use mcp") == "echoed:hi"


def test_orchestrator_can_respond_directly(monkeypatch):
    orchestrator = AgentOrchestrator([])
    monkeypatch.setattr(
        "src.orchestrator.query_llm",
        lambda prompt, model="gemini-2.0-flash": '{"action": "respond_to_user", "input": "At your service.", "next_action": ""}',
    )
    assert orchestrator.handle_message("hello") == "At your service."


def test_agent_matches_tool_aliases(monkeypatch):
    class AliasedTool(FakeTool):
        def name(self) -> str:
            return "fake__echo"

        def aliases(self):
            return ("echo", "fake/echo")

    agent = Agent(
        Name="Echo Agent",
        Description="Repeats input",
        Tools=[AliasedTool()],
        Model="gemini-2.0-flash",
    )
    monkeypatch.setattr(
        "src.agent.query_llm",
        lambda prompt, model="gemini-2.0-flash": '{"action": "echo", "args": "hi"}',
    )
    assert agent.process_input("say hi") == "echoed:hi"
