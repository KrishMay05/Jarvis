import os
import stat
import sys

import pytest

from src.config import (
    InvalidAPIKeyError,
    InvalidGoogleClientError,
    MissingAPIKeyError,
    apply_backup_llm_key,
    apply_llm_key,
    apply_google_oauth_config,
    describe_runtime,
    env_file_path,
    get_llm_settings,
    infer_provider,
    install_backup_llm_key,
    install_google_oauth_config,
    install_llm_key,
    list_llm_settings,
    persist_google_oauth_config,
    persist_llm_key,
)


def test_detects_gemini_key(monkeypatch):
    monkeypatch.delenv("JARVIS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("JARVIS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")

    settings = get_llm_settings()
    assert settings.provider == "gemini"
    assert settings.api_key == "gemini-test-key"
    assert settings.model == "gemini-2.0-flash"


def test_detects_openai_when_no_gemini(monkeypatch):
    monkeypatch.delenv("JARVIS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("JARVIS_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

    settings = get_llm_settings()
    assert settings.provider == "openai"
    assert settings.model == "gpt-4o-mini"


def test_provider_override_uses_shared_key(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("JARVIS_API_KEY", "sk-ant-shared")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_MODEL", raising=False)

    settings = get_llm_settings()
    assert settings.provider == "anthropic"
    assert settings.api_key == "sk-ant-shared"
    assert settings.model == "claude-sonnet-4-5"


def test_infers_openai_from_jarvis_api_key_prefix(monkeypatch):
    for var in (
        "JARVIS_LLM_PROVIDER",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "JARVIS_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("JARVIS_API_KEY", "sk-abc123")

    settings = get_llm_settings()
    assert settings.provider == "openai"


def test_model_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("JARVIS_MODEL", "gemini-2.5-flash")
    monkeypatch.delenv("JARVIS_LLM_PROVIDER", raising=False)

    assert get_llm_settings().model == "gemini-2.5-flash"


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_PROVIDER", "mistral")
    monkeypatch.setenv("JARVIS_API_KEY", "x")
    with pytest.raises(RuntimeError, match="Unknown JARVIS_LLM_PROVIDER"):
        get_llm_settings()


def test_missing_key_raises(monkeypatch):
    for var in (
        "JARVIS_LLM_PROVIDER",
        "JARVIS_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(MissingAPIKeyError, match="No AI API key found"):
        get_llm_settings()


def test_describe_runtime_mentions_one_key_tools(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.delenv("JARVIS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("JARVIS_MODEL", raising=False)
    text = describe_runtime()
    assert "gemini" in text
    assert "wttr.in" in text
    assert "chat" in text.lower()
    assert "memory" in text.lower()
    assert "automation" in text.lower()
    assert "computer" in text.lower()
    assert "mail" in text.lower()
    assert "calendar" in text.lower()
    assert "web ui" in text.lower()
    assert "paste" in text.lower()
    assert "optional backup key" in text.lower()
    assert "none required" in text.lower()
    assert "MCP:" in text
    assert "Memory:" in text
    assert "Automations:" in text
    assert "Auth:" in text


def test_list_llm_settings_one_key_is_enough(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-only")
    configured = list_llm_settings()
    assert [item.provider for item in configured] == ["openai"]
    assert get_llm_settings().provider == "openai"


def test_list_llm_settings_extra_keys_are_backups(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-primary")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-backup")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-backup")
    configured = list_llm_settings()
    assert [item.provider for item in configured] == ["gemini", "openai", "anthropic"]
    assert get_llm_settings().provider == "gemini"
    assert configured[1].model == "gpt-4o-mini"
    assert configured[2].model == "claude-sonnet-4-5"


def test_explicit_provider_stays_primary_when_other_keys_exist(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-primary")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-backup")
    configured = list_llm_settings()
    assert [item.provider for item in configured] == ["anthropic", "gemini"]


def test_model_override_applies_only_to_primary(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-primary")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-backup")
    monkeypatch.setenv("JARVIS_MODEL", "gemini-2.5-flash")
    configured = list_llm_settings()
    assert configured[0].model == "gemini-2.5-flash"
    assert configured[1].model == "gpt-4o-mini"


def test_shared_jarvis_api_key_does_not_duplicate_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("JARVIS_API_KEY", "sk-also-openai")
    configured = list_llm_settings()
    assert [item.provider for item in configured] == ["openai"]
    assert configured[0].api_key == "sk-openai"


def test_infer_provider_from_key_prefix():
    assert infer_provider("sk-ant-secret-key") == "anthropic"
    assert infer_provider("sk-openai-secret") == "openai"
    assert infer_provider("AIzaSyTestGemini") == "gemini"
    assert infer_provider("unknown-looking") == "gemini"


def test_apply_llm_key_sets_env_and_becomes_primary(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-already-there")
    settings = apply_llm_key("sk-openai-ui-paste-key", "auto")
    assert settings.provider == "openai"
    assert settings.api_key == "sk-openai-ui-paste-key"
    assert os.environ["OPENAI_API_KEY"] == "sk-openai-ui-paste-key"
    assert os.environ["JARVIS_LLM_PROVIDER"] == "openai"
    assert get_llm_settings().provider == "openai"


def test_persist_llm_key_updates_env_without_wiping_other_vars(tmp_path, monkeypatch):
    target = tmp_path / "custom.env"
    target.write_text(
        "# keep me\nGOOGLE_OAUTH_CLIENT_ID=abc.apps.googleusercontent.com\nGEMINI_API_KEY=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_ENV_PATH", str(tmp_path / "unused.env"))
    written = persist_llm_key("sk-openai-persist-key-1", path=target)
    assert written == target
    text = target.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "GOOGLE_OAUTH_CLIENT_ID=abc.apps.googleusercontent.com" in text
    assert "OPENAI_API_KEY=sk-openai-persist-key-1" in text
    assert "JARVIS_LLM_PROVIDER=openai" in text
    assert "sk-openai-persist-key-1" in text
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600


def test_install_llm_key_uses_jarvis_env_path(tmp_path, monkeypatch):
    target = tmp_path / "jarvis.env"
    monkeypatch.setenv("JARVIS_ENV_PATH", str(target))
    assert env_file_path() == target
    settings = install_llm_key("AIzaSyInstallKey99")
    assert settings.provider == "gemini"
    assert target.exists()
    assert "GEMINI_API_KEY=AIzaSyInstallKey99" in target.read_text(encoding="utf-8")


def test_apply_backup_llm_key_keeps_primary_and_pins_provider(monkeypatch):
    for var in (
        "JARVIS_LLM_PROVIDER",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
        "JARVIS_API_KEY",
        "JARVIS_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-primary-key")

    backup = apply_backup_llm_key("AIzaSyBackupGemini99", "auto")
    assert backup.provider == "gemini"
    assert backup.api_key == "AIzaSyBackupGemini99"
    assert os.environ["GEMINI_API_KEY"] == "AIzaSyBackupGemini99"
    assert os.environ["JARVIS_LLM_PROVIDER"] == "openai"
    configured = list_llm_settings()
    assert [item.provider for item in configured] == ["openai", "gemini"]
    assert get_llm_settings().provider == "openai"
    assert get_llm_settings().api_key == "sk-openai-primary-key"


def test_apply_backup_llm_key_rejects_same_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-primary-key")
    monkeypatch.setenv("JARVIS_LLM_PROVIDER", "openai")
    with pytest.raises(InvalidAPIKeyError, match="different provider"):
        apply_backup_llm_key("sk-openai-also-a-backup", "auto")


def test_install_backup_llm_key_uses_jarvis_env_path(tmp_path, monkeypatch):
    target = tmp_path / "backup.env"
    monkeypatch.setenv("JARVIS_ENV_PATH", str(target))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-primary-key")
    monkeypatch.delenv("JARVIS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    backup = install_backup_llm_key("AIzaSyInstallBackup9")
    assert backup.provider == "gemini"
    text = target.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=AIzaSyInstallBackup9" in text
    assert "JARVIS_LLM_PROVIDER=openai" in text
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600


def test_install_backup_llm_key_requires_primary(monkeypatch):
    for var in (
        "JARVIS_LLM_PROVIDER",
        "JARVIS_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(MissingAPIKeyError, match="No AI API key found"):
        install_backup_llm_key("AIzaSyNoPrimaryYet9")


def test_normalize_rejects_placeholder_and_short_keys():
    from src.config import normalize_api_key

    with pytest.raises(InvalidAPIKeyError, match="too short"):
        normalize_api_key("short")
    with pytest.raises(InvalidAPIKeyError, match="placeholder"):
        normalize_api_key("your-gemini-key")
    with pytest.raises(InvalidAPIKeyError, match="Paste an AI API key"):
        normalize_api_key("   ")


def test_describe_runtime_mentions_optional_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-backup")
    text = describe_runtime()
    assert "Fallback LLM: openai (gpt-4o-mini)" in text
    assert "optional extra key" in text.lower()
    assert "none required" in text.lower()


def test_describe_runtime_without_api_key(monkeypatch):
    text = describe_runtime()
    assert "LLM: not configured" in text
    assert "python main.py --serve" in text
    assert "--run-due fires reminders without an AI key" in text
    assert "wttr.in" in text
    assert "MCP:" in text
    assert "Memory:" in text
    assert "Automations:" in text
    assert "Auth:" in text
    assert "none required" in text.lower()


def test_main_status_without_api_key(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--status"])
    from main import main

    main()
    out = capsys.readouterr().out
    assert "LLM: not configured" in out
    assert "Tools:" in out
    assert "MCP:" in out


def test_main_status_with_key(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setattr(sys, "argv", ["main.py", "--status"])
    from main import main

    main()
    out = capsys.readouterr().out
    assert "LLM: gemini (gemini-2.0-flash)" in out
    assert "not configured" not in out


def test_normalize_google_client_id_rejects_placeholder():
    from src.config import normalize_google_client_id

    with pytest.raises(InvalidGoogleClientError, match="Paste a Google OAuth client ID"):
        normalize_google_client_id("   ")
    with pytest.raises(InvalidGoogleClientError, match="placeholder"):
        normalize_google_client_id("example.apps.googleusercontent.com")
    with pytest.raises(InvalidGoogleClientError, match="apps.googleusercontent.com"):
        normalize_google_client_id("not-a-google-client-id-value")
    assert (
        normalize_google_client_id('"abc.apps.googleusercontent.com"')
        == "abc.apps.googleusercontent.com"
    )


def test_install_google_oauth_config_sets_env_and_preserves_llm_key(tmp_path, monkeypatch):
    target = tmp_path / "mixed.env"
    target.write_text("# keep me\nOPENAI_API_KEY=sk-already-there\n", encoding="utf-8")
    monkeypatch.setenv("JARVIS_ENV_PATH", str(tmp_path / "unused.env"))
    cid = "123456789-config-test.apps.googleusercontent.com"
    written = persist_google_oauth_config(cid, "GOCSPX-optional-secret", path=target)
    assert written == target
    text = target.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "OPENAI_API_KEY=sk-already-there" in text
    assert f"GOOGLE_OAUTH_CLIENT_ID={cid}" in text
    assert "GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-optional-secret" in text
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600

    applied = apply_google_oauth_config(cid)
    assert applied == cid
    assert os.environ["GOOGLE_OAUTH_CLIENT_ID"] == cid


def test_install_google_oauth_config_uses_jarvis_env_path(tmp_path, monkeypatch):
    target = tmp_path / "google.env"
    monkeypatch.setenv("JARVIS_ENV_PATH", str(target))
    cid = "abc.apps.googleusercontent.com"
    assert install_google_oauth_config(cid) == cid
    assert target.exists()
    assert f"GOOGLE_OAUTH_CLIENT_ID={cid}" in target.read_text(encoding="utf-8")
    assert os.environ["GOOGLE_OAUTH_CLIENT_ID"] == cid
