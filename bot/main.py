"""Point d'entrée du bot : setup async complet + polling Telegram."""

from __future__ import annotations

import asyncio
from collections import deque

from telegram.ext import Application, MessageHandler, filters

from bot.config import load_settings
from bot.handlers import BotDeps, make_handler
from bot.llm.client import LLMClient
from bot.logging_conf import configure_logging, get_logger
from bot.memory.embeddings import Embedder
from bot.memory.manager import MemoryManager
from bot.search.searxng import SearxngClient
from bot.tasks.manager import TaskManager
from bot.tasks.scheduler import ReminderScheduler

log = get_logger(__name__)


async def _build_application() -> tuple[Application, BotDeps]:
    settings = load_settings()
    configure_logging(env=settings.env)
    log.info("startup", env=settings.env)

    embedder = Embedder(settings.ollama_base_url, settings.ollama_embed_model)
    memory = MemoryManager(settings.chroma_dir, embedder)

    tasks = TaskManager(settings.db_path)
    await tasks.init_schema()

    scheduler = ReminderScheduler(settings.scheduler_db_path, settings.telegram_bot_token)
    scheduler.start()

    llm = LLMClient(settings.ollama_base_url, settings.ollama_llm_model)
    search = SearxngClient(settings.searxng_base_url)

    deps = BotDeps(
        settings=settings,
        llm=llm,
        memory=memory,
        tasks=tasks,
        scheduler=scheduler,
        search=search,
        history=deque(),
    )

    application = Application.builder().token(settings.telegram_bot_token).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, make_handler(deps)))
    scheduler.attach_application(application)
    return application, deps


def main() -> None:
    """Entrée synchrone — `Application.run_polling` gère sa propre event loop."""
    application, _deps = asyncio.get_event_loop().run_until_complete(_build_application())
    application.run_polling(stop_signals=None)


if __name__ == "__main__":
    main()
