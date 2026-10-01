import pytest

from samvidhan.core.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.env == "dev"
    assert settings.embed_batch_size == 16
    assert settings.embed_max_length == 1024


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
    assert "test:test@" not in repr(settings)


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ValueError, match="database_url"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_invalid_value_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "xml")
    with pytest.raises(ValueError, match="log_format"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_chunk_limits_must_be_ordered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHUNK_TARGET_MAX_TOKENS", "900")  # above CHUNK_MAX_TOKENS=800
    with pytest.raises(ValueError, match="CHUNK_TARGET_MIN_TOKENS"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_ip_salt_must_be_set_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "prod")
    with pytest.raises(ValueError, match="IP_HASH_SALT"):
        Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.setenv("IP_HASH_SALT", "s3cret-random")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "10.0.0.5, 10.0.0.6")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.trusted_proxy_ips == ["10.0.0.5", "10.0.0.6"]
