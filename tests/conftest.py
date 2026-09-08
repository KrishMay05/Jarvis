import pytest


@pytest.fixture(autouse=True)
def isolate_local_state(monkeypatch, tmp_path):
    """Do not pick up a developer or CI home mcp.json / memory / automations / auth during tests."""
    monkeypatch.setenv("JARVIS_MCP_CONFIG", str(tmp_path / "missing-mcp.json"))
    monkeypatch.setenv("JARVIS_MEMORY_PATH", str(tmp_path / "memory.json"))
    monkeypatch.setenv("JARVIS_AUTOMATIONS_PATH", str(tmp_path / "automations.json"))
    monkeypatch.setenv("JARVIS_AUTH_PATH", str(tmp_path / "auth.json"))
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
