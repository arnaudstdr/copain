"""Chargement et validation des variables d'environnement.

Expose un objet `Settings` immuable utilisé partout dans le bot.
Aucun `os.getenv` ne doit être fait ailleurs qu'ici.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Levée quand une variable d'environnement requise est absente ou invalide."""


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    allowed_user_id: int

    ollama_base_url: str
    ollama_llm_model: str
    ollama_embed_model: str
    ollama_timeout_sec: float

    searxng_base_url: str

    data_dir: Path
    chroma_dir: Path
    db_path: Path
    scheduler_db_path: Path

    timezone: str

    briefing_hour: int
    briefing_minute: int
    home_lat: float
    home_lon: float
    home_city: str

    icloud_username: str
    icloud_app_password: str
    icloud_calendar_name: str

    proactivity_enabled: bool
    proactivity_window_start_hour: int
    proactivity_window_end_hour: int
    proactivity_daily_budget: int
    proactivity_check_interval_min: int
    proactivity_rain_cooldown_hours: int

    log_file_path: Path | None

    env: str

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


def _required(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ConfigError(f"Variable d'environnement manquante : {key}")
    return value


def _required_int(key: str) -> int:
    raw = _required(key)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} doit être un entier, reçu : {raw!r}") from exc


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} doit être un entier, reçu : {raw!r}") from exc


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} doit être un nombre, reçu : {raw!r}") from exc


def _env_bool(key: str, default: bool) -> bool:
    """Parse une variable d'env en booléen.

    Accepte `1/true/yes/on` pour `True`, `0/false/no/off` pour `False`
    (case-insensitive). Retourne `default` si absente ou vide.
    """
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{key} doit être un booléen (true/false), reçu : {raw!r}")


def _parse_log_file_path(data_dir: Path) -> Path | None:
    """Parse `LOG_FILE_PATH`.

    Défaut : `<data_dir>/logs/bot.log`. Valeur vide → `None` (pas de
    persistance fichier, seul le stdout reste actif).
    """
    raw = os.getenv("LOG_FILE_PATH")
    if raw is None:
        return (data_dir / "logs" / "bot.log").resolve()
    if raw.strip() == "":
        return None
    return Path(raw).resolve()


def load_settings() -> Settings:
    """Charge `.env` (si présent) puis construit l'objet Settings validé."""
    load_dotenv()

    data_dir = Path(os.getenv("DATA_DIR", "./data")).resolve()

    return Settings(
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
        allowed_user_id=_required_int("ALLOWED_USER_ID"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_llm_model=os.getenv("OLLAMA_LLM_MODEL", "gemma3:4b"),
        ollama_embed_model=os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
        ollama_timeout_sec=_env_float("OLLAMA_TIMEOUT_SEC", 120.0),
        searxng_base_url=os.getenv("SEARXNG_BASE_URL", "http://localhost:8080"),
        data_dir=data_dir,
        chroma_dir=Path(os.getenv("CHROMA_DIR", data_dir / "chroma")).resolve(),
        db_path=Path(os.getenv("DB_PATH", data_dir / "tasks.db")).resolve(),
        scheduler_db_path=Path(os.getenv("SCHEDULER_DB_PATH", data_dir / "scheduler.db")).resolve(),
        timezone=os.getenv("TZ", "Europe/Paris"),
        briefing_hour=_env_int("BRIEFING_HOUR", 8),
        briefing_minute=_env_int("BRIEFING_MINUTE", 0),
        home_lat=_env_float("HOME_LAT", 48.26),
        home_lon=_env_float("HOME_LON", 7.45),
        home_city=os.getenv("HOME_CITY", "Sélestat"),
        icloud_username=_required("ICLOUD_USERNAME"),
        icloud_app_password=_required("ICLOUD_APP_PASSWORD"),
        icloud_calendar_name=os.getenv("ICLOUD_CALENDAR_NAME", "Personnel"),
        proactivity_enabled=_env_bool("PROACTIVITY_ENABLED", False),
        proactivity_window_start_hour=_env_int("PROACTIVITY_WINDOW_START_HOUR", 8),
        proactivity_window_end_hour=_env_int("PROACTIVITY_WINDOW_END_HOUR", 21),
        proactivity_daily_budget=_env_int("PROACTIVITY_DAILY_BUDGET", 3),
        proactivity_check_interval_min=_env_int("PROACTIVITY_CHECK_INTERVAL_MIN", 30),
        proactivity_rain_cooldown_hours=_env_int("PROACTIVITY_RAIN_COOLDOWN_HOURS", 3),
        log_file_path=_parse_log_file_path(data_dir),
        env=os.getenv("ENV", "dev"),
    )
