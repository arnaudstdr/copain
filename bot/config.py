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

    searxng_base_url: str

    data_dir: Path
    chroma_dir: Path
    db_path: Path
    scheduler_db_path: Path

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
        searxng_base_url=os.getenv("SEARXNG_BASE_URL", "http://localhost:8080"),
        data_dir=data_dir,
        chroma_dir=Path(os.getenv("CHROMA_DIR", data_dir / "chroma")).resolve(),
        db_path=Path(os.getenv("DB_PATH", data_dir / "tasks.db")).resolve(),
        scheduler_db_path=Path(
            os.getenv("SCHEDULER_DB_PATH", data_dir / "scheduler.db")
        ).resolve(),
        env=os.getenv("ENV", "dev"),
    )
