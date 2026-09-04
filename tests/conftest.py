import pytest


@pytest.fixture(autouse=True)
def isolate_mcp_config(monkeypatch, tmp_path):
    """Do not pick up a developer or CI home mcp.json during tests."""
    monkeypatch.setenv("JARVIS_MCP_CONFIG", str(tmp_path / "missing-mcp.json"))
