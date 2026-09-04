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


def _scripted(replies):
    remaining = list(replies)

    def fake(prompt, model="gemini-2.0-flash"):
        if not remaining:
            raise AssertionError("unexpected extra LLM call")
        return remaining.pop(0)

    return fake


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


def test_chat_agent_replies_without_tools(monkeypatch):
    agent = Agent(
        Name="Chat Agent",
        Description="General conversation",
        Tools=[],
        Model="gemini-2.0-flash",
    )
    monkeypatch.setattr(
        "src.agent.query_llm",
        lambda prompt, model="gemini-2.0-flash": "Hello — how can I help?",
    )
    assert agent.process_input("hi") == {
        "action": "respond_to_user",
        "args": "Hello — how can I help?",
    }


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
        _scripted(
            [
                '{"action": "Echo Agent", "input": "hi", "next_action": ""}',
                '{"action": "respond_to_user", "input": "echoed:hi", "next_action": ""}',
            ]
        ),
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
        _scripted(
            [
                '{"action": "mcp agent", "input": "hi", "next_action": ""}',
                '{"action": "respond_to_user", "input": "echoed:hi", "next_action": ""}',
            ]
        ),
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


def test_chat_agent_direct_reply_ends_the_turn(monkeypatch):
    chat = Agent(
        Name="Chat Agent",
        Description="General conversation",
        Tools=[],
        Model="gemini-2.0-flash",
    )
    orchestrator = AgentOrchestrator([chat])
    monkeypatch.setattr(
        "src.orchestrator.query_llm",
        lambda prompt, model="gemini-2.0-flash": '{"action": "Chat Agent", "input": "hello", "next_action": ""}',
    )
    monkeypatch.setattr(
        "src.agent.query_llm",
        lambda prompt, model="gemini-2.0-flash": "Good day. How may I assist?",
    )
    assert orchestrator.handle_message("hello") == "Good day. How may I assist?"


def test_orchestrator_continues_after_tool_result(monkeypatch):
    weather = Agent(
        Name="Weather Agent",
        Description="Weather",
        Tools=[FakeTool()],
        Model="gemini-2.0-flash",
    )
    time_agent = Agent(
        Name="Time Agent",
        Description="Time",
        Tools=[FakeTool()],
        Model="gemini-2.0-flash",
    )
    orchestrator = AgentOrchestrator([weather, time_agent])
    monkeypatch.setattr(
        "src.orchestrator.query_llm",
        _scripted(
            [
                '{"action": "Weather Agent", "input": "NYC", "next_action": "Time Agent"}',
                '{"action": "Time Agent", "input": "London", "next_action": ""}',
                '{"action": "respond_to_user", "input": "NYC is echoed; London time next.", "next_action": ""}',
            ]
        ),
    )
    monkeypatch.setattr(
        "src.agent.query_llm",
        lambda prompt, model="gemini-2.0-flash": '{"action": "echo", "args": "ok"}',
    )
    assert (
        orchestrator.handle_message("weather in NYC and time in London")
        == "NYC is echoed; London time next."
    )


def test_orchestrator_invalid_json_falls_back_to_user_reply(monkeypatch):
    orchestrator = AgentOrchestrator([])
    monkeypatch.setattr(
        "src.orchestrator.query_llm",
        lambda prompt, model="gemini-2.0-flash": "not json at all",
    )
    assert orchestrator.handle_message("hello") == "not json at all"
