"""Tests de `bot.sentry_setup` : no-op sans DSN, init avec DSN."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from bot.config import Settings
from bot.sentry_setup import capture_exception, configure_sentry


def _settings(sentry_dsn: str | None, traces: float = 0.0) -> Settings:
    from pathlib import Path

    return Settings(
        telegram_bot_token="t",
        allowed_user_id=1,
        ollama_base_url="x",
        ollama_llm_model="m",
        ollama_embed_model="e",
        ollama_timeout_sec=60.0,
        ollama_num_ctx=32768,
        ollama_fallback_model=None,
        ollama_fallback_base_url=None,
        ollama_fallback_timeout_sec=60.0,
        ollama_fallback_num_ctx=8192,
        searxng_base_url="x",
        data_dir=Path("/tmp"),
        chroma_dir=Path("/tmp"),
        db_path=Path("/tmp"),
        scheduler_db_path=Path("/tmp"),
        timezone="Europe/Paris",
        briefing_hour=8,
        briefing_minute=0,
        home_lat=0.0,
        home_lon=0.0,
        home_city="x",
        icloud_username="u",
        icloud_app_password="p",
        icloud_calendar_name="c",
        proactivity_enabled=False,
        proactivity_window_start_hour=8,
        proactivity_window_end_hour=21,
        proactivity_daily_budget=3,
        proactivity_check_interval_min=30,
        proactivity_rain_cooldown_hours=3,
        fuel_default_radius_km=10.0,
        nominatim_user_agent="x",
        cache_llm_ttl_sec=0.0,
        cache_llm_max_size=1,
        cache_searxng_ttl_sec=0.0,
        cache_searxng_max_size=1,
        log_file_path=None,
        sentry_dsn=sentry_dsn,
        sentry_environment=None,
        sentry_release=None,
        sentry_traces_sample_rate=traces,
        env="dev",
    )


def test_configure_sentry_noop_without_dsn() -> None:
    assert configure_sentry(_settings(None)) is False


def test_configure_sentry_inits_with_dsn() -> None:
    with patch("sentry_sdk.init") as init_mock:
        assert configure_sentry(_settings("https://fake@sentry.io/1")) is True
        init_mock.assert_called_once()
        kwargs = init_mock.call_args.kwargs
        assert kwargs["dsn"] == "https://fake@sentry.io/1"
        assert kwargs["send_default_pii"] is False


def test_capture_exception_noop_when_sentry_not_imported() -> None:
    """Appel safe même si Sentry n'est pas init : ne crash pas."""
    capture_exception(RuntimeError("boom"), foo="bar")


def test_capture_exception_forwards_context() -> None:
    fake_scope = MagicMock()
    with (
        patch("sentry_sdk.new_scope") as push,
        patch("sentry_sdk.capture_exception") as capture,
    ):
        push.return_value.__enter__.return_value = fake_scope
        err = RuntimeError("boom")
        capture_exception(err, chat_id=42, source="test")
        fake_scope.set_extra.assert_any_call("chat_id", 42)
        fake_scope.set_extra.assert_any_call("source", "test")
        capture.assert_called_once_with(err)
