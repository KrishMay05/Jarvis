from src.assistant import build_orchestrator
from src.config import LLMSettings


def test_build_orchestrator_includes_research_weather_and_time():
    settings = LLMSettings(provider="gemini", api_key="test", model="gemini-2.0-flash")
    orchestrator = build_orchestrator(settings)
    names = {agent.name for agent in orchestrator.agents}
    assert names == {"Weather Agent", "Time Agent", "Research Agent"}
    for agent in orchestrator.agents:
        assert agent.model == "gemini-2.0-flash"
        assert agent.tools
