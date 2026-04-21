"""Création et configuration de l'`AsyncEngine` SQLAlchemy partagé.

Tous les managers (`TaskManager`, `FeedManager`) qui opèrent sur `tasks.db`
doivent partager le même engine — sinon les pools concurrents peuvent se
renvoyer `database is locked` sous charge (finding CODE_REVIEW #5).

Le mode `journal_mode=WAL` est activé après création : il autorise plusieurs
lectures concurrentes avec une écriture, et réduit la fenêtre de contention
sur SQLite.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from bot.logging_conf import get_logger

log = get_logger(__name__)


def create_shared_engine(db_path: Path) -> AsyncEngine:
    """Crée un `AsyncEngine` sur le chemin donné (SQLite + aiosqlite)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)


async def enable_wal_mode(engine: AsyncEngine) -> None:
    """Active le mode WAL pour la connexion SQLite sous-jacente.

    À appeler **une fois** au démarrage, avant la première requête concurrente.
    """
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA journal_mode=WAL"))
        mode = result.scalar_one()
    log.info("sqlite_journal_mode_set", mode=str(mode))
