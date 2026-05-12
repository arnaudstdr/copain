"""Couche transport HTTP (FastAPI).

Trois endpoints, tous authentifiés par le header `X-API-Key` validé contre
`settings.api_key` :

- `POST /ask`         — message texte → réponse complète (pas de streaming SSE).
- `POST /ask/image`   — message + image base64 → analyse multimodale.
- `GET  /notifications` — file des notifications poussées (briefing, rappels,
  proactivité). Lit puis marque les entrées comme lues.

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


class BriefingCard(BaseModel):
    text: str
    created_at: str


class DashboardResponse(BaseModel):
    weather: WeatherCard | None
    next_event: NextEventCard | None
    today_tasks: list[TaskCard]
    unread_notifications: int
    briefing: BriefingCard | None


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


# Mapping meta.intent → cards à rafraîchir côté front. Les intents purement
# informatifs (answer, search, weather, fuel, memory, feed) n'altèrent aucune
# card du dashboard ; l'UI affiche juste la réponse texte (bulle éphémère).
_REFRESH_BY_INTENT: dict[str, list[str]] = {
    "task": ["today_tasks", "unread_notifications"],
    "event": ["next_event"],
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
        return FileResponse(STATIC_DIR / "index.html")

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
    briefing = (
        BriefingCard(
            text=snap.latest_briefing.text,
            created_at=snap.latest_briefing.created_at.isoformat(),
        )
        if snap.latest_briefing is not None
        else None
    )
    return DashboardResponse(
        weather=weather,
        next_event=next_event,
        today_tasks=tasks,
        unread_notifications=snap.unread_notifications,
        briefing=briefing,
    )
