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
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bot.llm.client import LLMError, LLMTimeoutError
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


class NotificationItem(BaseModel):
    id: int
    text: str
    created_at: str


class NotificationsResponse(BaseModel):
    notifications: list[NotificationItem]


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

    @app.get("/config", include_in_schema=False)
    async def get_config(settings: Settings = Depends(get_settings_dep)) -> dict[str, str]:
        return {"api_key": settings.api_key}

    @app.post(
        "/ask",
        response_model=AskResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def ask(payload: AskRequest, deps: BotDeps = Depends(get_deps)) -> AskResponse:
        log.info("ask_received", preview=payload.message[:80])
        try:
            reply = await process_message(payload.message, deps)
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
        return AskResponse(response=reply)

    @app.post(
        "/ask/image",
        response_model=AskResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def ask_image(
        payload: AskImageRequest,
        deps: BotDeps = Depends(get_deps),
    ) -> AskResponse:
        try:
            image_bytes = base64.b64decode(payload.image_b64, validate=True)
        except (ValueError, TypeError) as exc:
            log.warning("ask_image_invalid_base64", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="image_b64 must be valid base64",
            ) from exc

        log.info(
            "ask_image_received",
            preview=payload.message[:80],
            size=len(image_bytes),
            media_type=payload.media_type,
        )
        try:
            reply = await process_message(payload.message, deps, images=[image_bytes])
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
        return AskResponse(response=reply)

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

    return app
