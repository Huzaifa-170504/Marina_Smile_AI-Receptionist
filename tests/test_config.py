import pytest
from pydantic import ValidationError

from app.core.config import Settings


BASE_ENV = {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SECRET_KEY": "test-secret",
    "GEMINI_API_KEY": "test-api-key",
}


def test_settings_load_required_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DASHBOARD_ALLOWED_ORIGINS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.gemini_embedding_model == "gemini-embedding-2"
    assert settings.gemini_embedding_dimensions == 768
    assert settings.rag_match_threshold == 0.60
    assert settings.dashboard_origins == []


def test_dashboard_origins_are_environment_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(
        "DASHBOARD_ALLOWED_ORIGINS",
        "http://localhost:3000, https://dashboard.example.com/ ",
    )

    settings = Settings(_env_file=None)

    assert settings.dashboard_origins == [
        "http://localhost:3000",
        "https://dashboard.example.com",
    ]


def test_settings_reject_incompatible_embedding_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GEMINI_EMBEDDING_DIMENSIONS", "1536")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_enabled_demo_requires_a_strong_server_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEMO_ENABLED", "true")
    monkeypatch.setenv("DEMO_API_TOKEN", "too-short")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
