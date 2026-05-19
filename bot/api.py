"""Couche transport HTTP (FastAPI).

Trois endpoints, tous authentifiés par le header `X-API-Key` validé contre
`settings.api_key` :

- `POST /ask`         — message texte → réponse complète (pas de streaming SSE).
- `POST /ask/image`   — message + image base64 → analyse multimodale.
- `GET  /notifications` — file des notifications poussées (rappels, proactivité).
  Lit puis marque les entrées comme lues.

La logique métier (pipeline LLM + routing `<meta>` + side effects) vit dans
`bot.pipeline.process_message`. Cette couche reste fine : auth + I/O + appel
du pipeline. Les dépendances vivantes (LLMClient, MemoryManager, etc.) sont
attachées à `app.state` au moment du lifespan, et exposées via les
dependencies `get_deps` / `get_notifications`.
"""

from __future__ import annotations

import base64
import pathlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bot.dashboard import DashboardSnapshot, build_dashboard
from bot.llm.client import LLMError, LLMTimeoutError
from bot.llm.parser import Meta
from bot.logging_conf import get_logger
from bot.pipeline import BotDeps, process_message
from bot.sentry_setup import capture_exception

if TYPE_CHECKING:
    from bot.config import Settings
    from bot.notifications.store import NotificationStore

log = get_logger(__name__)

STATIC_DIR = pathlib.Path(__file__).parent / "static"


# --- Schémas Pydantic --------------------------------------------------------


class AskRequest(BaseModel):
    message: str


class AskImageRequest(BaseModel):
    message: str
    image_b64: str = Field(description="Image encodée en base64 (sans préfixe data:).")
    media_type: str = Field(
        description="Type MIME (ex: image/jpeg, image/png). Informatif pour les logs.",
    )


class AskResponse(BaseModel):
    response: str
    intent: str = "answer"
    refresh_cards: list[str] = Field(default_factory=list)


class NotificationItem(BaseModel):
    id: int
    text: str
    created_at: str


class NotificationsResponse(BaseModel):
    notifications: list[NotificationItem]


# --- Dashboard schemas ------------------------------------------------------


class WeatherCard(BaseModel):
    city: str
    temp_current: float
    temp_min: float
    temp_max: float
    description: str
    precipitation_mm: float
    wind_kmh: float


class NextEventCard(BaseModel):
    title: str
    start: str
    end: str
    location: str | None
    calendar_name: str


class TaskCard(BaseModel):
    id: int
    content: str
    due_at: str | None


class BudgetEnvelopeCard(BaseModel):
    category: str
    label: str
    allocated_eur: float
    spent_eur: float
    remaining_eur: float  # peut être négatif si dépassement
    is_overrun: bool


class BudgetCard(BaseModel):
    month: str  # ISO date du 1er du mois (YYYY-MM-DD)
    income_eur: float
    spent_eur: float  # punctual + recurring_tick + saving_tick
    remaining_eur: float  # prévisionnel (revenu - sorties reelles - pending)
    saved_this_year_eur: float
    pending_recurring_count: int
    has_overdue: bool
    envelopes: list[BudgetEnvelopeCard] = Field(default_factory=list)
    has_envelope_overrun: bool = False


class DashboardResponse(BaseModel):
    weather: WeatherCard | None
    next_event: NextEventCard | None
    today_tasks: list[TaskCard]
    unread_notifications: int
    budget: BudgetCard | None


# --- Location schemas -------------------------------------------------------


class LocationEventRequest(BaseModel):
    event: Literal["arrived", "left"]
    place: str = Field(min_length=1, max_length=50)
    lat: float | None = None
    lon: float | None = None
    at: str | None = Field(
        default=None,
        description="Timestamp ISO 8601 du moment réel de la transition côté iPhone. "
        "Défaut : now() côté serveur.",
    )


class LocationEventResponse(BaseModel):
    recorded: bool
    current_place: str | None


# --- Tasks schemas ---------------------------------------------------------


class TasksListResponse(BaseModel):
    tasks: list[TaskCard]


class TaskMutationResponse(BaseModel):
    ok: bool


# --- Thoughts schemas ------------------------------------------------------


class ThoughtItem(BaseModel):
    id: int
    content: str
    kind: str | None
    created_at: str  # ISO


class ThoughtsListResponse(BaseModel):
    thoughts: list[ThoughtItem]


# --- News schemas ----------------------------------------------------------


class NewsLatestResponse(BaseModel):
    markdown: str
    fetched_at: str  # ISO 8601 UTC


# --- Budget schemas --------------------------------------------------------


class BudgetTransaction(BaseModel):
    id: int
    kind: str  # punctual | recurring_tick | saving_tick | income
    amount_eur: float
    label: str
    category: str | None
    recurring_key: str | None
    occurred_on: str  # ISO date


class BudgetPendingItem(BaseModel):
    key: str
    label: str
    amount_eur: float
    day: int
    kind: str  # expense | saving
    is_overdue: bool


class BudgetEnvelopeDetail(BaseModel):
    category: str
    label: str
    allocated_eur: float
    spent_eur: float
    remaining_eur: float
    overrun_eur: float
    is_overrun: bool


class BudgetMonthDetail(BaseModel):
    month: str  # ISO date du 1er du mois
    currency: str
    income_eur: float
    spent_punctual_eur: float
    spent_recurring_eur: float
    saved_this_month_eur: float
    saved_this_year_eur: float
    remaining_eur: float
    transactions: list[BudgetTransaction]
    pending: list[BudgetPendingItem]
    envelopes: list[BudgetEnvelopeDetail] = Field(default_factory=list)


# --- Weather / Events detail schemas ---------------------------------------


class HourlyForecastItem(BaseModel):
    time: str  # ISO
    temp_c: float
    precipitation_mm: float
    precipitation_probability_pct: int
    description: str


class DailyForecastItem(BaseModel):
    date: str  # ISO date
    temp_min: float
    temp_max: float
    temp_current: float | None
    precipitation_mm: float
    wind_kmh_max: float
    description: str


class WeatherForecastResponse(BaseModel):
    city: str
    hourly: list[HourlyForecastItem]  # 24h glissantes à partir de now
    daily: list[DailyForecastItem]  # 7 prochains jours


class CalendarEventItem(BaseModel):
    uid: str
    title: str
    start: str  # ISO
    end: str
    location: str | None
    description: str | None
    calendar_name: str


class EventsListResponse(BaseModel):
    events: list[CalendarEventItem]


# Mapping meta.intent → cards à rafraîchir côté front. Les intents purement
# informatifs (answer, search, weather, fuel, memory, feed) n'altèrent aucune
# card du dashboard ; l'UI affiche juste la réponse texte (bulle éphémère).
_REFRESH_BY_INTENT: dict[str, list[str]] = {
    "task": ["today_tasks", "unread_notifications"],
    "event": ["next_event"],
    "expense": ["budget"],
}


def _refresh_cards_for(meta: Meta) -> list[str]:
    """Retourne la liste des noms de cards à recharger après une action.

    - `intent=task` → la card tâches change ; si la due_str produit un rappel,
      la card notifications peut aussi changer (on rafraîchit les deux).
    - `intent=event` avec `action=create` → la card prochain évent change.
      `action=list` n'altère rien (lecture pure).
    - Autres intents → aucune card concernée, retour vide.
    """
    if meta["intent"] == "event":
        return ["next_event"] if meta["event"]["action"] == "create" else []
    return list(_REFRESH_BY_INTENT.get(meta["intent"], []))


# --- Container des dépendances vivantes attaché à app.state ------------------


@dataclass
class AppState:
    settings: Settings
    deps: BotDeps
    notifications: NotificationStore


# --- Dependencies ------------------------------------------------------------


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.copain
    return state


def get_settings_dep(state: AppState = Depends(get_state)) -> Settings:
    return state.settings


def get_deps(state: AppState = Depends(get_state)) -> BotDeps:
    return state.deps


def get_notifications(state: AppState = Depends(get_state)) -> NotificationStore:
    return state.notifications


async def verify_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings_dep),
) -> None:
    """Vérifie le header `X-API-Key`. Log un warning avec l'IP source si invalide."""
    if x_api_key != settings.api_key:
        client_ip = request.client.host if request.client else None
        log.warning(
            "api_access_denied",
            ip=client_ip,
            has_header=x_api_key is not None,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )


# --- Endpoints ---------------------------------------------------------------


def create_app(state: AppState) -> FastAPI:
    """Construit l'application FastAPI à partir d'un `AppState` déjà initialisé.

    Le lifespan ne s'occupe que de logger le démarrage/arrêt : l'init des
    dépendances (engine, scheduler, calendar, schémas) est faite dans
    `bot.main.main()` AVANT l'instanciation de l'app, pour conserver une
    séparation nette entre setup et serveur HTTP.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        log.info("api_lifespan_startup", port=state.settings.api_port)
        try:
            yield
        finally:
            log.info("api_lifespan_shutdown")

    app = FastAPI(
        title="copain",
        description="Assistant personnel HTTP (FastAPI) — exposé via Tailscale.",
        lifespan=lifespan,
    )
    app.state.copain = state

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=FileResponse, include_in_schema=False)
    async def chat_ui() -> FileResponse:
        # Cache-Control no-store : la PWA iOS cache le HTML très agressivement
        # (parfois même sans service worker). Sans ce header, un rebuild +
        # redéploiement ne suffit pas à faire disparaître l'ancien dashboard.
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    _TOUCH_ICON = STATIC_DIR / "icon-1024.png"

    @app.get("/apple-touch-icon.png", response_class=FileResponse, include_in_schema=False)
    @app.get(
        "/apple-touch-icon-precomposed.png", response_class=FileResponse, include_in_schema=False
    )
    @app.get("/apple-touch-icon-120x120.png", response_class=FileResponse, include_in_schema=False)
    @app.get(
        "/apple-touch-icon-120x120-precomposed.png",
        response_class=FileResponse,
        include_in_schema=False,
    )
    async def apple_touch_icon() -> FileResponse:
        return FileResponse(_TOUCH_ICON, media_type="image/png")

    @app.get("/config", include_in_schema=False)
    async def get_config(settings: Settings = Depends(get_settings_dep)) -> dict[str, str]:
        return {"api_key": settings.api_key}

    @app.post(
        "/ask",
        response_model=AskResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def ask(
        payload: AskRequest,
        deps: BotDeps = Depends(get_deps),
        x_source: str | None = Header(default=None, alias="X-Source"),
    ) -> AskResponse:
        voice_mode = x_source == "siri"
        log.info("ask_received", preview=payload.message[:80], voice_mode=voice_mode)
        try:
            reply, meta = await process_message(payload.message, deps, voice_mode=voice_mode)
        except LLMTimeoutError:
            log.warning("llm_timeout")
            return AskResponse(
                response=(
                    "Le modèle met trop longtemps à répondre pour l'instant. "
                    "Réessaie dans quelques secondes."
                )
            )
        except LLMError as exc:
            log.error("llm_error", error=str(exc))
            return AskResponse(
                response=(
                    "Le modèle LLM a un souci côté serveur pour l'instant. Réessaie dans un moment."
                )
            )
        except Exception as exc:
            log.exception("ask_failed", error=str(exc))
            capture_exception(exc, source="api_ask")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal error",
            ) from exc
        return AskResponse(
            response=reply, intent=meta["intent"], refresh_cards=_refresh_cards_for(meta)
        )

    @app.post(
        "/ask/image",
        response_model=AskResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def ask_image(
        payload: AskImageRequest,
        deps: BotDeps = Depends(get_deps),
        x_source: str | None = Header(default=None, alias="X-Source"),
    ) -> AskResponse:
        try:
            image_bytes = base64.b64decode(payload.image_b64, validate=True)
        except (ValueError, TypeError) as exc:
            log.warning("ask_image_invalid_base64", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="image_b64 must be valid base64",
            ) from exc

        voice_mode = x_source == "siri"
        log.info(
            "ask_image_received",
            preview=payload.message[:80],
            size=len(image_bytes),
            media_type=payload.media_type,
            voice_mode=voice_mode,
        )
        try:
            reply, meta = await process_message(
                payload.message, deps, images=[image_bytes], voice_mode=voice_mode
            )
        except LLMTimeoutError:
            log.warning("llm_timeout", kind="image")
            return AskResponse(
                response=(
                    "Le modèle met trop longtemps à analyser l'image. "
                    "Réessaie dans quelques secondes."
                )
            )
        except LLMError as exc:
            log.error("llm_error", kind="image", error=str(exc))
            return AskResponse(
                response=(
                    "Le modèle LLM a un souci côté serveur pour l'instant. Réessaie dans un moment."
                )
            )
        except Exception as exc:
            log.exception("ask_image_failed", error=str(exc))
            capture_exception(exc, source="api_ask_image")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal error",
            ) from exc
        return AskResponse(
            response=reply, intent=meta["intent"], refresh_cards=_refresh_cards_for(meta)
        )

    @app.get(
        "/notifications",
        response_model=NotificationsResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def list_notifications(
        notifications: NotificationStore = Depends(get_notifications),
    ) -> NotificationsResponse:
        unread = await notifications.get_unread()
        items = [
            NotificationItem(id=n.id, text=n.text, created_at=n.created_at.isoformat())
            for n in unread
        ]
        if items:
            await notifications.mark_read([n.id for n in unread])
        log.info("notifications_polled", returned=len(items))
        return NotificationsResponse(notifications=items)

    @app.get(
        "/dashboard",
        response_model=DashboardResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def get_dashboard(
        deps: BotDeps = Depends(get_deps),
        notifications: NotificationStore = Depends(get_notifications),
    ) -> DashboardResponse:
        snapshot = await build_dashboard(deps, notifications)
        return _snapshot_to_response(snapshot)

    @app.post(
        "/event/location",
        response_model=LocationEventResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def record_location_event(
        payload: LocationEventRequest,
        deps: BotDeps = Depends(get_deps),
    ) -> LocationEventResponse:
        """Enregistre une transition de localisation envoyée par iOS.

        L'app Shortcuts iOS POST sur cet endpoint à chaque arrivée /
        départ d'une géofence (maison, bureau). L'event est persisté
        dans `location_events` et la position courante est dérivée
        par le store ; elle sera ensuite injectée dans le system prompt
        à chaque appel `/ask` ou `/ask/image`.

        Déclenche aussi `ProactivityService.on_location_event` qui peut
        pousser une notif (ex: briefing retour au départ du bureau le
        soir). L'appel est fail-soft : un crash de la proactivité ne
        doit pas empêcher l'enregistrement de l'event.
        """
        occurred_at = _parse_iso_or_now(payload.at)
        await deps.location_events.record_event(
            event_type=payload.event,
            place=payload.place,
            lat=payload.lat,
            lon=payload.lon,
            occurred_at=occurred_at,
        )
        current = await deps.location_events.get_current_location()
        log.info(
            "location_event_received",
            event_type=payload.event,
            place=payload.place,
            current=current.place if current else None,
        )

        # Trigger proactivité event-driven (fail-soft, déjà wrappé d'un
        # try/except large par on_location_event lui-même).
        await deps.proactivity.on_location_event(payload.event, payload.place)

        return LocationEventResponse(
            recorded=True,
            current_place=current.place if current else None,
        )

    @app.get(
        "/tasks",
        response_model=TasksListResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def list_tasks(deps: BotDeps = Depends(get_deps)) -> TasksListResponse:
        """Liste toutes les tâches en cours (non terminées), triées par échéance.

        Utilisé par la PWA pour afficher l'overlay des tâches (cochage et
        suppression). Les tâches sans `due_at` sont placées à la fin
        (cf. l'order_by de `TaskManager.list_pending`).
        """
        pending = await deps.tasks.list_pending()
        items = [
            TaskCard(
                id=t.id,
                content=t.content,
                due_at=t.due_at.isoformat() if t.due_at is not None else None,
            )
            for t in pending
        ]
        return TasksListResponse(tasks=items)

    @app.post(
        "/tasks/{task_id}/complete",
        response_model=TaskMutationResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def complete_task(
        task_id: int, deps: BotDeps = Depends(get_deps)
    ) -> TaskMutationResponse:
        """Marque une tâche comme terminée. 404 si introuvable ou déjà terminée."""
        ok = await deps.tasks.complete(task_id)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found or already completed",
            )
        log.info("task_completed", task_id=task_id)
        return TaskMutationResponse(ok=True)

    @app.delete(
        "/tasks/{task_id}",
        response_model=TaskMutationResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def delete_task(task_id: int, deps: BotDeps = Depends(get_deps)) -> TaskMutationResponse:
        """Supprime une tâche. 404 si introuvable."""
        ok = await deps.tasks.delete(task_id)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found",
            )
        log.info("task_deleted", task_id=task_id)
        return TaskMutationResponse(ok=True)

    @app.get(
        "/news/latest",
        response_model=NewsLatestResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def news_latest(deps: BotDeps = Depends(get_deps)) -> NewsLatestResponse:
        """Récupère et résume les actus 24h pour la card Actu du dashboard.

        Lit les topics + blocklist depuis `data/profile.yaml` (section
        `news_topics.daily_briefing`), interroge SearXNG via NewsCurator
        et demande au LLM de curer + résumer. Pas de cache côté backend :
        SearXNG est déjà caché côté client, et un appel n'a lieu qu'au
        tap utilisateur (pas en boucle).
        """
        from bot.news.client import extract_news_config

        topics, blocklist = extract_news_config(deps.profile.data)
        if not topics:
            return NewsLatestResponse(
                markdown=(
                    "Aucun topic configuré dans `data/profile.yaml` "
                    "(section `news_topics.daily_briefing`)."
                ),
                fetched_at=datetime.now(UTC).isoformat(),
            )

        try:
            markdown = await deps.news.fetch_top_news(topics=topics, domains_blocklist=blocklist)
        except Exception as exc:
            log.exception("news_latest_failed", error=str(exc))
            capture_exception(exc, source="api_news_latest")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Impossible de récupérer les actus pour le moment.",
            ) from exc

        return NewsLatestResponse(
            markdown=markdown or "Aucune actu pertinente sur les dernières 24h.",
            fetched_at=datetime.now(UTC).isoformat(),
        )

    @app.get(
        "/thoughts",
        response_model=ThoughtsListResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def list_thoughts(
        deps: BotDeps = Depends(get_deps),
        since: str | None = None,
        limit: int = 50,
    ) -> ThoughtsListResponse:
        """Liste les dépôts cognitifs récents (intent `depot`).

        Filtre optionnel `since` (ISO 8601). `limit` plafonné à 200 pour
        éviter les payloads trop gros. Tri chronologique inverse (les
        dépôts les plus récents en premier).
        """
        capped_limit = max(1, min(limit, 200))
        if since is not None:
            try:
                since_dt = datetime.fromisoformat(since)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"`since` doit être un timestamp ISO 8601 valide : {since!r}",
                ) from exc
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=UTC)
            rows = await deps.thoughts.list_since(since_dt, limit=capped_limit)
        else:
            rows = await deps.thoughts.list_recent(limit=capped_limit)

        items = [
            ThoughtItem(
                id=t.id,
                content=t.content,
                kind=t.kind,
                created_at=t.created_at.isoformat(),
            )
            for t in rows
        ]
        return ThoughtsListResponse(thoughts=items)

    @app.get(
        "/budget",
        response_model=BudgetMonthDetail,
        dependencies=[Depends(verify_api_key)],
    )
    async def get_budget(
        deps: BotDeps = Depends(get_deps),
    ) -> BudgetMonthDetail:
        """État détaillé du mois courant : transactions + pending récurrentes.

        Alimente l'overlay de la card Budget. Le calcul réutilise
        `compute_budget` (mêmes invariants que le dashboard) pour rester
        cohérent — si une saisie disparaît / apparaît, les deux vues le
        voient en même temps.

        Si la section `finances` du YAML est absente, retourne une réponse
        "vide" avec `currency=EUR` et toutes les agrégations à 0 plutôt
        qu'une 404 : l'UI peut afficher un message d'aide vers le YAML.
        """
        from bot.finance.budget import compute_budget
        from bot.finance.config import extract_finance_config

        tz = ZoneInfo(deps.settings.timezone)
        today_d = datetime.now(tz).date()
        month_start = today_d.replace(day=1)
        cfg = extract_finance_config(deps.profile.data)
        month_rows = await deps.expenses.list_for_month(month_start)
        year_savings = await deps.expenses.list_savings_for_year(today_d.year)
        summary = compute_budget(
            config=cfg,
            month_expenses=month_rows,
            year_savings=year_savings,
            today=today_d,
        )

        transactions = [
            BudgetTransaction(
                id=e.id,
                kind=e.kind,
                amount_eur=e.amount_cents / 100,
                label=e.label,
                category=e.category,
                recurring_key=e.recurring_key,
                occurred_on=e.occurred_on.isoformat(),
            )
            for e in month_rows
        ]
        pending = [
            BudgetPendingItem(
                key=p.key,
                label=p.label,
                amount_eur=p.amount_cents / 100,
                day=p.day,
                kind=p.kind,
                is_overdue=p.is_overdue,
            )
            for p in summary.pending_recurring
        ]
        envelopes_detail = [
            BudgetEnvelopeDetail(
                category=env.category,
                label=env.label,
                allocated_eur=env.allocated_cents / 100,
                spent_eur=env.spent_cents / 100,
                remaining_eur=env.remaining_cents / 100,
                overrun_eur=env.overrun_cents / 100,
                is_overrun=env.is_overrun,
            )
            for env in summary.envelopes
        ]
        return BudgetMonthDetail(
            month=summary.month.isoformat(),
            currency=cfg.currency,
            income_eur=summary.income_cents / 100,
            spent_punctual_eur=summary.spent_punctual_cents / 100,
            spent_recurring_eur=summary.spent_recurring_cents / 100,
            saved_this_month_eur=summary.saved_this_month_cents / 100,
            saved_this_year_eur=summary.saved_this_year_cents / 100,
            remaining_eur=summary.remaining_cents / 100,
            transactions=transactions,
            pending=pending,
            envelopes=envelopes_detail,
        )

    @app.get(
        "/weather/forecast",
        response_model=WeatherForecastResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def weather_forecast(
        deps: BotDeps = Depends(get_deps),
        days: int = 7,
        hours: int = 24,
    ) -> WeatherForecastResponse:
        """Prévisions météo détaillées (horaire 24h + quotidien 7 jours).

        Le lieu est choisi via la même règle que la card dashboard : si
        l'utilisateur est au bureau (`current_location.place == "work"`),
        on utilise `WORK_*`, sinon `HOME_*`. Pas d'appel LLM — donnée
        brute Open-Meteo.
        """
        presence = await deps.location_events.get_current_location()
        if presence is not None and presence.place == "work":
            lat, lon, city = (
                deps.settings.work_lat,
                deps.settings.work_lon,
                deps.settings.work_city,
            )
        else:
            lat, lon, city = (
                deps.settings.home_lat,
                deps.settings.home_lon,
                deps.settings.home_city,
            )

        hourly_raw = await deps.weather.get_hourly_forecast(
            lat=lat, lon=lon, hours_ahead=max(1, min(hours, 48))
        )
        daily_raw = await deps.weather.get_forecast(
            lat=lat, lon=lon, city=city, days=max(1, min(days, 16))
        )

        hourly = [
            HourlyForecastItem(
                time=h.time.isoformat(),
                temp_c=h.temp_c,
                precipitation_mm=h.precipitation_mm,
                precipitation_probability_pct=h.precipitation_probability_pct,
                description=h.description,
            )
            for h in hourly_raw
        ]
        daily = [
            DailyForecastItem(
                date=d.date.isoformat(),
                temp_min=d.temp_min,
                temp_max=d.temp_max,
                temp_current=d.temp_current,
                precipitation_mm=d.precipitation_mm,
                wind_kmh_max=d.wind_kmh_max,
                description=d.description,
            )
            for d in daily_raw
        ]
        return WeatherForecastResponse(city=city, hourly=hourly, daily=daily)

    @app.get(
        "/events",
        response_model=EventsListResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def list_events(
        deps: BotDeps = Depends(get_deps),
        days: int = 7,
    ) -> EventsListResponse:
        """Liste des évènements iCloud à venir (tous calendriers agrégés).

        Pas d'appel LLM — donnée brute CalDAV. Le front groupe par jour
        pour l'affichage de l'overlay.
        """
        if not deps.calendar.is_connected:
            return EventsListResponse(events=[])
        events_raw = await deps.calendar.list_all_upcoming(days=max(1, min(days, 60)))
        items = [
            CalendarEventItem(
                uid=e.uid,
                title=e.title,
                start=e.start.isoformat(),
                end=e.end.isoformat(),
                location=e.location,
                description=e.description,
                calendar_name=e.calendar_name,
            )
            for e in events_raw
        ]
        return EventsListResponse(events=items)

    return app


def _parse_iso_or_now(raw: str | None) -> datetime:
    """Parse un timestamp ISO 8601 ou retourne now(UTC) si absent / invalide.

    En cas de format invalide on logge un warning mais on accepte l'event
    (avec un timestamp serveur) plutôt que de renvoyer 400 — la perte d'un
    event de localisation à cause d'un format d'horodatage est inutilement
    coûteuse côté UX. La validation stricte côté Pydantic se limite aux
    autres champs.
    """
    if raw is None:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        log.warning("location_at_invalid_iso", raw=raw)
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _snapshot_to_response(snap: DashboardSnapshot) -> DashboardResponse:
    weather = (
        WeatherCard(
            city=snap.weather.city,
            temp_current=snap.weather.temp_current,
            temp_min=snap.weather.temp_min,
            temp_max=snap.weather.temp_max,
            description=snap.weather.description,
            precipitation_mm=snap.weather.precipitation_mm,
            wind_kmh=snap.weather.wind_kmh,
        )
        if snap.weather is not None
        else None
    )
    next_event = (
        NextEventCard(
            title=snap.next_event.title,
            start=snap.next_event.start.isoformat(),
            end=snap.next_event.end.isoformat(),
            location=snap.next_event.location,
            calendar_name=snap.next_event.calendar_name,
        )
        if snap.next_event is not None
        else None
    )
    tasks = [
        TaskCard(
            id=t.id,
            content=t.content,
            due_at=t.due_at.isoformat() if t.due_at is not None else None,
        )
        for t in snap.today_tasks
    ]
    budget: BudgetCard | None = None
    if snap.budget is not None:
        b = snap.budget
        spent_cents = b.spent_punctual_cents + b.spent_recurring_cents + b.saved_this_month_cents
        budget = BudgetCard(
            month=b.month.isoformat(),
            income_eur=b.income_cents / 100,
            spent_eur=spent_cents / 100,
            remaining_eur=b.remaining_cents / 100,
            saved_this_year_eur=b.saved_this_year_cents / 100,
            pending_recurring_count=b.pending_recurring_count,
            has_overdue=b.has_overdue,
            envelopes=[
                BudgetEnvelopeCard(
                    category=env.category,
                    label=env.label,
                    allocated_eur=env.allocated_cents / 100,
                    spent_eur=env.spent_cents / 100,
                    remaining_eur=env.remaining_cents / 100,
                    is_overrun=env.is_overrun,
                )
                for env in b.envelopes
            ],
            has_envelope_overrun=b.has_envelope_overrun,
        )
    return DashboardResponse(
        weather=weather,
        next_event=next_event,
        today_tasks=tasks,
        unread_notifications=snap.unread_notifications,
        budget=budget,
    )
