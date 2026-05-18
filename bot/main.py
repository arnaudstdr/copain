"""Point d'entrée : initialise toutes les dépendances puis lance uvicorn.

L'app FastAPI est construite à partir d'un `AppState` déjà fully wired ;
voir `bot.api.create_app`. Les schémas SQLite, le mode WAL, la connexion
CalDAV et le scheduler sont initialisés ici (de manière asynchrone via
`asyncio.run`) avant le démarrage du serveur, pour rester proche de l'ancien
`post_init` PTB tout en évitant que des requêtes HTTP arrivent avant que
les services soient prêts.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections import deque
from collections.abc import Awaitable, Callable

import uvicorn

from bot.api import AppState, create_app
from bot.briefing.weather import OpenMeteoClient
from bot.calendar.client import ICloudCalendarClient, ICloudCalendarError
from bot.config import Settings, load_settings
from bot.db import create_shared_engine, enable_wal_mode
from bot.fuel.client import FuelClient
from bot.fuel.geocoding import NominatimClient
from bot.llm.client import LLMClient
from bot.locations.store import LocationEventStore
from bot.logging_conf import configure_logging, get_logger
from bot.memory.embeddings import Embedder
from bot.memory.manager import MemoryManager
from bot.news.client import NewsCurator
from bot.notifications.pushover import PushoverClient
from bot.notifications.store import NotificationStore
from bot.pipeline import MAX_HISTORY, BotDeps
from bot.proactivity import models as _proactivity_models  # noqa: F401 — enregistre la table
from bot.proactivity.service import ProactivityService
from bot.profile import load_profile
from bot.rss.fetcher import RssFetcher
from bot.rss.manager import FeedAlreadyExists, FeedManager
from bot.search.searxng import SearxngClient
from bot.sentry_setup import configure_sentry
from bot.tasks.manager import TaskManager
from bot.tasks.scheduler import ReminderScheduler
from bot.thoughts.manager import ThoughtManager

log = get_logger(__name__)

DEFAULT_FEEDS: tuple[tuple[str, str, str], ...] = (
    ("The Verge", "https://www.theverge.com/rss/index.xml", "tech"),
)

PROACTIVITY_JOB_ID = "proactivity-tick"


async def _seed_default_feeds(rss: FeedManager) -> None:
    if await rss.count() > 0:
        return
    for name, url, category in DEFAULT_FEEDS:
        try:
            await rss.add(url=url, name=name, category=category)
        except FeedAlreadyExists:
            continue
    log.info("default_feeds_seeded", count=len(DEFAULT_FEEDS))


async def _build_state(
    settings: Settings,
) -> tuple[AppState, list[Callable[[], Awaitable[None]]]]:
    """Instancie toutes les dépendances et retourne l'AppState + la liste des cleanups."""
    embedder = Embedder(settings.ollama_base_url, settings.ollama_embed_model)
    weather = OpenMeteoClient(timezone=settings.timezone)
    scheduler = ReminderScheduler(
        settings.scheduler_db_path,
        notifications_db_path=settings.db_path,
        timezone=settings.timezone,
        pushover_token=settings.pushover_token,
        pushover_user=settings.pushover_user,
    )
    engine = create_shared_engine(settings.db_path)
    tasks = TaskManager(engine, scheduler=scheduler)
    thoughts = ThoughtManager(engine)
    rss = FeedManager(engine)
    rss_fetcher = RssFetcher()
    pushover = PushoverClient(token=settings.pushover_token, user=settings.pushover_user)
    notifications = NotificationStore(engine, pushover=pushover)
    llm = LLMClient(
        settings.ollama_base_url,
        settings.ollama_llm_model,
        timeout=settings.ollama_timeout_sec,
        num_ctx=settings.ollama_num_ctx,
        cache_ttl_sec=settings.cache_llm_ttl_sec,
        cache_max_size=settings.cache_llm_max_size,
        fallback_model=settings.ollama_fallback_model,
        fallback_base_url=settings.ollama_fallback_base_url,
        fallback_timeout_sec=settings.ollama_fallback_timeout_sec,
        fallback_num_ctx=settings.ollama_fallback_num_ctx,
    )
    calendar = ICloudCalendarClient(
        username=settings.icloud_username,
        app_password=settings.icloud_app_password,
        calendar_name=settings.icloud_calendar_name,
        timezone=settings.timezone,
    )
    fuel = FuelClient()
    geocoder = NominatimClient(user_agent=settings.nominatim_user_agent)
    proactivity = ProactivityService(
        settings=settings,
        weather=weather,
        calendar=calendar,
        engine=engine,
        notifications=notifications,
    )
    search = SearxngClient(
        settings.searxng_base_url,
        cache_ttl_sec=settings.cache_searxng_ttl_sec,
        cache_max_size=settings.cache_searxng_max_size,
    )

    profile = load_profile(settings.profile_path)
    news = NewsCurator(searxng=search, llm=llm)

    location_events = LocationEventStore(engine)

    deps = BotDeps(
        settings=settings,
        llm=llm,
        memory=MemoryManager(settings.chroma_dir, embedder),
        tasks=tasks,
        thoughts=thoughts,
        scheduler=scheduler,
        search=search,
        rss=rss,
        rss_fetcher=rss_fetcher,
        calendar=calendar,
        fuel=fuel,
        geocoder=geocoder,
        weather=weather,
        news=news,
        profile=profile,
        location_events=location_events,
        proactivity=proactivity,
        history=deque(maxlen=MAX_HISTORY),
    )

    # Initialisations asynchrones — équivalent de l'ancien `post_init` PTB.
    await enable_wal_mode(engine)
    await tasks.init_schema()
    await thoughts.init_schema()
    await rss.init_schema()
    await notifications.init_schema()
    await location_events.init_schema()
    await _seed_default_feeds(rss)
    try:
        await calendar.connect()
    except ICloudCalendarError as exc:
        log.warning("calendar_connect_failed", error=str(exc))

    scheduler.start()

    async def _proactivity_tick_job() -> None:
        await proactivity.tick()

    if settings.proactivity_enabled:
        scheduler.add_interval_job(
            job_id=PROACTIVITY_JOB_ID,
            func=_proactivity_tick_job,
            minutes=settings.proactivity_check_interval_min,
        )
        log.info(
            "proactivity_job_scheduled",
            interval_min=settings.proactivity_check_interval_min,
            window=f"{settings.proactivity_window_start_hour}-{settings.proactivity_window_end_hour}",
            budget=settings.proactivity_daily_budget,
        )

    state = AppState(settings=settings, deps=deps, notifications=notifications)

    async def _cleanup() -> None:
        scheduler.shutdown()
        await search.aclose()
        await weather.aclose()
        await fuel.aclose()
        await geocoder.aclose()
        await engine.dispose()
        log.info("shutdown_done")

    return state, [_cleanup]


def main() -> None:
    settings = load_settings()
    configure_logging(env=settings.env, log_file_path=settings.log_file_path)
    sentry_on = configure_sentry(settings)
    log.info("startup", env=settings.env, sentry=sentry_on, port=settings.api_port)
    if settings.pushover_token and settings.pushover_user:
        log.info("pushover_configured")
    else:
        log.warning("pushover_not_configured", hint="set PUSHOVER_TOKEN and PUSHOVER_USER in .env")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    state, cleanups = loop.run_until_complete(_build_state(settings))
    app = create_app(state)

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.api_port,
        log_config=None,  # on garde structlog comme seule source de logs
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    # uvicorn gère lui-même SIGINT/SIGTERM, on enchaîne juste les cleanups.
    def _install_signal_handlers() -> None:
        def _on_signal(sig: signal.Signals) -> None:
            log.info("signal_received", signal=sig.name)

        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, _on_signal, s)

    # Windows / certains conteneurs minimaux : signaux non supportés, on laisse uvicorn gérer.
    with contextlib.suppress(NotImplementedError):
        _install_signal_handlers()

    try:
        loop.run_until_complete(server.serve())
    finally:
        for cleanup in cleanups:
            loop.run_until_complete(cleanup())
        loop.close()


if __name__ == "__main__":
    main()
