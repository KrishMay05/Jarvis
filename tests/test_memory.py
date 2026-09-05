import json

from src.agent import Agent
from src.memory.store import (
    MemoryStore,
    default_memory_path,
    jarvis_home,
    memory_status_line,
)
from src.orchestrator import AgentOrchestrator
from src.tools.memory_tool import MemoryTool


def test_remember_recall_forget_roundtrip(tmp_path):
    store = MemoryStore(tmp_path / "memory.json")
    assert "Remembered" in store.remember("Lives in Austin")
    listed = store.recall()
    assert "Austin" in listed
    assert "Forgot" in store.forget("Austin")
    assert "No durable memories" in store.recall()


def test_remember_deduplicates_case_insensitively(tmp_path):
    store = MemoryStore(tmp_path / "memory.json")
    first = store.remember("Prefers Celsius")
    second = store.remember("prefers celsius")
    assert first.startswith("Remembered")
    assert second.startswith("Already remembered")
    assert len(store.facts) == 1


def test_store_reloads_from_disk(tmp_path):
    path = tmp_path / "memory.json"
    first = MemoryStore(path)
    first.remember("Name is Krish")
    first.record_exchange("hello", "At your service.")

    second = MemoryStore(path)
    assert any("Krish" in fact.text for fact in second.facts)
    assert second.turns[0].role == "user"
    assert second.turns[1].role == "assistant"
    assert "Krish" in second.prompt_context()
    assert "hello" in second.prompt_context()


def test_corrupt_memory_file_starts_empty(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text("{not json", encoding="utf-8")
    store = MemoryStore(path)
    assert store.facts == []
    assert store.turns == []
    store.remember("Safe after corruption")
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["facts"][0]["text"] == "Safe after corruption"


def test_memory_paths_honor_env(monkeypatch, tmp_path):
    home = tmp_path / "custom-home"
    monkeypatch.setenv("JARVIS_HOME", str(home))
    monkeypatch.delenv("JARVIS_MEMORY_PATH", raising=False)
    assert jarvis_home() == home
    assert default_memory_path() == home / "memory.json"

    explicit = tmp_path / "elsewhere" / "notes.json"
    monkeypatch.setenv("JARVIS_MEMORY_PATH", str(explicit))
    assert default_memory_path() == explicit


def test_forget_matches_id(tmp_path):
    store = MemoryStore(tmp_path / "memory.json")
    store.remember("Works remotely on Fridays")
    fact_id = store.facts[0].id
    assert fact_id in store.forget(fact_id)
    assert store.facts == []


def test_memory_tool_parses_phrases_and_dicts(tmp_path):
    tool = MemoryTool(MemoryStore(tmp_path / "memory.json"))
    assert "Remembered" in tool.use("I drink tea in the morning")
    assert "tea" in tool.use("recall tea")
    assert "Forgot" in tool.use({"action": "forget", "query": "tea"})
    assert "No durable memories" in tool.use({"action": "list"})


def test_memory_tool_aliases_include_remember():
    assert "remember" in MemoryTool().aliases()


def test_status_line_reports_empty_and_counts(tmp_path):
    path = tmp_path / "memory.json"
    empty = MemoryStore(path)
    assert "empty" in empty.status_line()
    empty.remember("Timezone is America/Chicago")
    empty.record_exchange("hi", "hello")
    text = memory_status_line(empty)
    assert "1 fact" in text
    assert "2 turn" in text
    assert str(path) in text


def test_orchestrator_records_exchanges(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "memory.json")
    orchestrator = AgentOrchestrator([], memory_store=store)
    monkeypatch.setattr(
        "src.orchestrator.query_llm",
        lambda prompt, model="gemini-2.0-flash": (
            '{"action": "respond_to_user", "input": "At your service.", "next_action": ""}'
        ),
    )
    assert orchestrator.handle_message("hello") == "At your service."
    assert [turn.role for turn in store.turns] == ["user", "assistant"]
    assert store.turns[0].text == "hello"


def test_orchestrator_prompt_includes_durable_facts(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "memory.json")
    store.remember("Home city is Austin")
    seen: list[str] = []

    def fake(prompt, model="gemini-2.0-flash"):
        seen.append(prompt)
        return '{"action": "respond_to_user", "input": "ok", "next_action": ""}'

    orchestrator = AgentOrchestrator([], memory_store=store)
    monkeypatch.setattr("src.orchestrator.query_llm", fake)
    orchestrator.handle_message("what is the weather")
    assert seen
    assert "Home city is Austin" in seen[0]
    assert "Memory Agent" in seen[0]


def test_chat_agent_prompt_includes_durable_facts(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "memory.json")
    store.remember("User is named Krish")
    seen: list[str] = []

    def fake(prompt, model="gemini-2.0-flash"):
        seen.append(prompt)
        return "Hello Krish."

    agent = Agent(
        Name="Chat Agent",
        Description="General conversation",
        Tools=[],
        Model="gemini-2.0-flash",
        memory_store=store,
    )
    monkeypatch.setattr("src.agent.query_llm", fake)
    result = agent.process_input("hi")
    assert result["args"] == "Hello Krish."
    assert "User is named Krish" in seen[0]


def test_memory_agent_can_store_a_fact(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "memory.json")
    agent = Agent(
        Name="Memory Agent",
        Description="Remembers facts",
        Tools=[MemoryTool(store)],
        Model="gemini-2.0-flash",
    )
    monkeypatch.setattr(
        "src.agent.query_llm",
        lambda prompt, model="gemini-2.0-flash": (
            '{"action": "remember", "args": "Lives in Austin"}'
        ),
    )
    assert "Remembered" in agent.process_input("remember I live in Austin")
    assert store.facts[0].text == "Lives in Austin"
