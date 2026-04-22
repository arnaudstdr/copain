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
