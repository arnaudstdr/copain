"""Point d'entrée du bot : setup + polling Telegram.

`Application.run_polling()` (PTB v21) gère sa propre event loop. On évite donc
d'appeler `asyncio.get_event_loop()` en amont : l'init asynchrone (schéma DB,
scheduler start) est branchée via `post_init`, la libération via `post_shutdown`.
"""

from __future__ import annotations

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


def main() -> None:
    settings = load_settings()
    configure_logging(env=settings.env)
    log.info("startup", env=settings.env)

    embedder = Embedder(settings.ollama_base_url, settings.ollama_embed_model)
    deps = BotDeps(
        settings=settings,
        llm=LLMClient(settings.ollama_base_url, settings.ollama_llm_model),
        memory=MemoryManager(settings.chroma_dir, embedder),
        tasks=TaskManager(settings.db_path),
        scheduler=ReminderScheduler(settings.scheduler_db_path, settings.telegram_bot_token),
        search=SearxngClient(settings.searxng_base_url),
        history=deque(),
    )

    async def _post_init(app: Application) -> None:
        await deps.tasks.init_schema()
        deps.scheduler.start()
        deps.scheduler.attach_application(app)
        log.info("post_init_done")

    async def _post_shutdown(_app: Application) -> None:
        deps.scheduler.shutdown()
        await deps.search.aclose()
        await deps.tasks.dispose()
        log.info("post_shutdown_done")

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, make_handler(deps))
    )
    application.run_polling()


if __name__ == "__main__":
    main()
