import pytest


@pytest.fixture(autouse=True)
def isolate_local_state(monkeypatch, tmp_path):
    """Do not pick up a developer or CI home mcp.json / memory.json during tests."""
    monkeypatch.setenv("JARVIS_MCP_CONFIG", str(tmp_path / "missing-mcp.json"))
    monkeypatch.setenv("JARVIS_MEMORY_PATH", str(tmp_path / "memory.json"))
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))
