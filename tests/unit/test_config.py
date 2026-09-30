import pytest

from samvidhan.core.config import Settings


def test_defaults_point_db_at_port_5433() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert ":5433/" in settings.database_dsn
    assert settings.env == "dev"


def test_env_overrides_and_csv_cors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    monkeypatch.setenv("FINAL_K", "7")
    monkeypatch.setenv("LOG_FORMAT", "json")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.cors_origins == ["http://a.test", "http://b.test"]
    assert settings.final_k == 7
    assert settings.log_format == "json"


def test_secrets_are_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_supersecret")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "gsk_supersecret" not in repr(settings)
    assert "samvidhan:samvidhan@" not in repr(settings)


def test_invalid_value_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "xml")
    with pytest.raises(ValueError, match="log_format"):
        Settings(_env_file=None)  # type: ignore[call-arg]
