"""Tests de `bot.config.load_settings` — valeurs par défaut et parsing booléen."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.config import ConfigError, _env_bool, load_settings


def _minimal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remplit les variables requises pour que `load_settings` n'échoue pas."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("ALLOWED_USER_ID", "42")
    monkeypatch.setenv("ICLOUD_USERNAME", "arnaud@example.com")
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "aaaa-bbbb-cccc-dddd")


def test_proactivity_defaults_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    for var in (
        "PROACTIVITY_ENABLED",
        "PROACTIVITY_WINDOW_START_HOUR",
        "PROACTIVITY_WINDOW_END_HOUR",
        "PROACTIVITY_DAILY_BUDGET",
        "PROACTIVITY_CHECK_INTERVAL_MIN",
        "PROACTIVITY_RAIN_COOLDOWN_HOURS",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = load_settings()
    assert settings.proactivity_enabled is False
    assert settings.proactivity_window_start_hour == 8
    assert settings.proactivity_window_end_hour == 21
    assert settings.proactivity_daily_budget == 3
    assert settings.proactivity_check_interval_min == 30
    assert settings.proactivity_rain_cooldown_hours == 3


def test_proactivity_custom_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("PROACTIVITY_ENABLED", "true")
    monkeypatch.setenv("PROACTIVITY_WINDOW_START_HOUR", "9")
    monkeypatch.setenv("PROACTIVITY_WINDOW_END_HOUR", "22")
    monkeypatch.setenv("PROACTIVITY_DAILY_BUDGET", "5")
    monkeypatch.setenv("PROACTIVITY_CHECK_INTERVAL_MIN", "15")
    monkeypatch.setenv("PROACTIVITY_RAIN_COOLDOWN_HOURS", "2")

    settings = load_settings()
    assert settings.proactivity_enabled is True
    assert settings.proactivity_window_start_hour == 9
    assert settings.proactivity_window_end_hour == 22
    assert settings.proactivity_daily_budget == 5
    assert settings.proactivity_check_interval_min == 15
    assert settings.proactivity_rain_cooldown_hours == 2


def test_env_bool_accepts_multiple_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("X", truthy)
        assert _env_bool("X", False) is True
    for falsy in ("0", "false", "FALSE", "no", "off"):
        monkeypatch.setenv("X", falsy)
        assert _env_bool("X", True) is False


def test_env_bool_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X", "maybe")
    with pytest.raises(ConfigError, match="booléen"):
        _env_bool("X", False)


def test_log_file_path_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LOG_FILE_PATH", raising=False)

    settings = load_settings()
    assert settings.log_file_path == (tmp_path / "logs" / "bot.log").resolve()


def test_log_file_path_empty_disables_file_logging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_FILE_PATH", "")

    settings = load_settings()
    assert settings.log_file_path is None


def test_log_file_path_explicit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_FILE_PATH", str(tmp_path / "custom" / "bot.log"))

    settings = load_settings()
    assert settings.log_file_path == (tmp_path / "custom" / "bot.log").resolve()


def test_ollama_num_ctx_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    settings = load_settings()
    assert settings.ollama_num_ctx == 32768


def test_ollama_num_ctx_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_NUM_CTX", "16384")
    settings = load_settings()
    assert settings.ollama_num_ctx == 16384


def test_cache_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    for var in (
        "CACHE_LLM_TTL_SEC",
        "CACHE_LLM_MAX_SIZE",
        "CACHE_SEARXNG_TTL_SEC",
        "CACHE_SEARXNG_MAX_SIZE",
    ):
        monkeypatch.delenv(var, raising=False)
    settings = load_settings()
    assert settings.cache_llm_ttl_sec == 21600.0
    assert settings.cache_llm_max_size == 128
    assert settings.cache_searxng_ttl_sec == 3600.0
    assert settings.cache_searxng_max_size == 128


def test_cache_custom_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("CACHE_LLM_TTL_SEC", "600")
    monkeypatch.setenv("CACHE_LLM_MAX_SIZE", "32")
    monkeypatch.setenv("CACHE_SEARXNG_TTL_SEC", "120")
    monkeypatch.setenv("CACHE_SEARXNG_MAX_SIZE", "16")
    settings = load_settings()
    assert settings.cache_llm_ttl_sec == 600.0
    assert settings.cache_llm_max_size == 32
    assert settings.cache_searxng_ttl_sec == 120.0
    assert settings.cache_searxng_max_size == 16


def test_sentry_defaults_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    for var in ("SENTRY_DSN", "SENTRY_ENVIRONMENT", "SENTRY_RELEASE", "SENTRY_TRACES_SAMPLE_RATE"):
        monkeypatch.delenv(var, raising=False)
    settings = load_settings()
    assert settings.sentry_dsn is None
    assert settings.sentry_environment is None
    assert settings.sentry_release is None
    assert settings.sentry_traces_sample_rate == 0.0


def test_sentry_custom_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/1234")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "prod")
    monkeypatch.setenv("SENTRY_RELEASE", "0.9.0")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")
    settings = load_settings()
    assert settings.sentry_dsn == "https://fake@sentry.io/1234"
    assert settings.sentry_environment == "prod"
    assert settings.sentry_release == "0.9.0"
    assert settings.sentry_traces_sample_rate == 0.25
