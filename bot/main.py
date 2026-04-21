"""Point d'entrée du bot : setup + polling Telegram.

`Application.run_polling()` (PTB v21) gère sa propre event loop. On évite donc
d'appeler `asyncio.get_event_loop()` en amont : l'init asynchrone (schéma DB,
scheduler start) est branchée via `post_init`, la libération via `post_shutdown`.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from telegram.ext import Application, MessageHandler, filters

from bot.briefing.service import BriefingService
from bot.briefing.weather import OpenMeteoClient
from bot.config import load_settings
from bot.handlers import BotDeps, make_handler, make_photo_handler
from bot.llm.client import LLMClient
from bot.logging_conf import configure_logging, get_logger
from bot.memory.embeddings import Embedder
from bot.memory.manager import MemoryManager
from bot.rss.fetcher import RssFetcher
from bot.rss.manager import FeedAlreadyExists, FeedManager
from bot.search.searxng import SearxngClient
from bot.tasks.manager import TaskManager
from bot.tasks.scheduler import ReminderScheduler

log = get_logger(__name__)

DEFAULT_FEEDS: tuple[tuple[str, str, str], ...] = (
    ("The Verge", "https://www.theverge.com/rss/index.xml", "tech"),
    ("ZDNet", "https://www.zdnet.com/news/rss.xml", "tech"),
)

BRIEFING_JOB_ID = "daily-briefing"


async def _seed_default_feeds(rss: FeedManager) -> None:
    if await rss.count() > 0:
        return
    for name, url, category in DEFAULT_FEEDS:
        try:
            await rss.add(url=url, name=name, category=category)
        except FeedAlreadyExists:
            continue
    log.info("default_feeds_seeded", count=len(DEFAULT_FEEDS))


def main() -> None:
    settings = load_settings()
    configure_logging(env=settings.env)
    log.info("startup", env=settings.env)

    embedder = Embedder(settings.ollama_base_url, settings.ollama_embed_model)
    weather = OpenMeteoClient(timezone=settings.timezone)
    tasks = TaskManager(settings.db_path)
    rss = FeedManager(settings.db_path)
    rss_fetcher = RssFetcher()
    llm = LLMClient(settings.ollama_base_url, settings.ollama_llm_model)

    deps = BotDeps(
        settings=settings,
        llm=llm,
        memory=MemoryManager(settings.chroma_dir, embedder),
        tasks=tasks,
        scheduler=ReminderScheduler(
            settings.scheduler_db_path,
            settings.telegram_bot_token,
            timezone=settings.timezone,
        ),
        search=SearxngClient(settings.searxng_base_url),
        rss=rss,
        rss_fetcher=rss_fetcher,
        briefing=BriefingService(
            settings=settings,
            weather=weather,
            tasks=tasks,
            rss=rss,
            rss_fetcher=rss_fetcher,
            llm=llm,
        ),
        history=deque(),
    )

    async def _daily_briefing_job() -> None:
        await deps.briefing.send_daily(chat_id=settings.allowed_user_id)

    async def _post_init(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        await deps.tasks.init_schema()
        await deps.rss.init_schema()
        await _seed_default_feeds(deps.rss)
        deps.scheduler.start()
        deps.scheduler.attach_application(app)
        deps.scheduler.add_cron_job(
            job_id=BRIEFING_JOB_ID,
            func=_daily_briefing_job,
            hour=settings.briefing_hour,
            minute=settings.briefing_minute,
        )
        log.info("post_init_done")

    async def _post_shutdown(_app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        deps.scheduler.shutdown()
        await deps.search.aclose()
        await weather.aclose()
        await deps.rss.dispose()
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
    application.add_handler(MessageHandler(filters.PHOTO, make_photo_handler(deps)))
    application.run_polling()


if __name__ == "__main__":
    main()
