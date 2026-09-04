from src.assistant import build_orchestrator
from src.config import LLMSettings


def test_build_orchestrator_includes_research_weather_time_and_chat():
    settings = LLMSettings(provider="gemini", api_key="test", model="gemini-2.0-flash")
    orchestrator = build_orchestrator(settings)
    try:
        by_name = {agent.name: agent for agent in orchestrator.agents}
        assert set(by_name) == {
            "Weather Agent",
            "Time Agent",
            "Research Agent",
            "Chat Agent",
        }
        for name, agent in by_name.items():
            assert agent.model == "gemini-2.0-flash"
            if name == "Chat Agent":
                assert agent.tools == []
            else:
                assert agent.tools
    finally:
        orchestrator.close()
