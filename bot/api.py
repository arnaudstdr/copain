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
import hmac
import json
import os
import pathlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from bot.dashboard import DashboardSnapshot, build_dashboard
from bot.finance.csv_export import build_expenses_csv
from bot.llm.client import LLMError, LLMTimeoutError
from bot.llm.parser import VALID_DEPOT_KINDS, Meta
from bot.logging_conf import get_logger
from bot.pipeline import (
    BotDeps,
    loop_suffix,
    parse_when_to_date,
    process_message,
    process_message_stream,
    record_depot,
)
from bot.sentry_setup import capture_exception

if TYPE_CHECKING:
    from bot.config import Settings
    from bot.finance.models import Expense
    from bot.notifications.store import NotificationStore

log = get_logger(__name__)

# Build du front React (Vite), unique interface web depuis le cutover (front
# vanilla supprimé). Absent en dev pur (on passe par `vite dev`) et en CI/clone
# frais (gitignoré) : dans ce cas aucun serving statique n'est monté (`/` → 404,
# warning au boot). Présent (image Docker, `make run` local après build) : le
# catch-all SPAStaticFiles sert `/`. Cf. .claude/plans/frontend-react-vite/.
FRONTEND_DIST = pathlib.Path(__file__).parent.parent / "frontend" / "dist"


class SPAStaticFiles(StaticFiles):
    """`StaticFiles` servant le build Vite avec fallback SPA + `no-store` sur l'index.

    Deux écarts par rapport au `StaticFiles(html=True)` standard :

    - **Fallback SPA** : tout chemin inconnu sous `/` retombe sur `index.html`
      (200) au lieu de 404. Les vraies routes API restent prioritaires car
      FastAPI les résout avant ce mount catch-all.
    - **`index.html` en `no-store`** (comme le serving vanilla) : Safari iOS
      cache le shell HTML très agressivement, un redéploiement doit pouvoir le
      remplacer. Les assets, eux, sont hashés par Vite → cache long via les
      en-têtes par défaut de `StaticFiles`.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        # StaticFiles lève `starlette.exceptions.HTTPException` (classe parente) ;
        # capturer `fastapi.HTTPException` (sous-classe) ne l'attraperait pas.
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if os.path.basename(full_path) == "index.html":
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


# --- Schémas Pydantic --------------------------------------------------------

# Bornes de taille des entrées. Mono-utilisateur derrière Tailscale, donc pas
# une défense anti-DoS publique : juste un garde-fou pour qu'un payload
# aberrant (texte de plusieurs Mo, image géante) soit rejeté par un 422 net
# avant d'atteindre le LLM ou le décodage base64, plutôt que de faire planter
# le process. ~20 Mo de base64 ≈ 15 Mo d'image décodée, large pour une photo.
MAX_MESSAGE_CHARS = 10_000
MAX_IMAGE_B64_CHARS = 20_000_000


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class AskImageRequest(BaseModel):
    # Légende optionnelle : une capture (ticket, transaction Revolut) est
    # souvent envoyée sans texte. Le pipeline substitue un prompt par défaut
    # quand le message est vide (cf. process_message).
    message: str = Field(min_length=0, max_length=MAX_MESSAGE_CHARS)
    image_b64: str = Field(
        min_length=1,
        max_length=MAX_IMAGE_B64_CHARS,
        description="Image encodée en base64 (sans préfixe data:).",
    )
    media_type: str = Field(
        description="Type MIME (ex: image/jpeg, image/png). Informatif pour les logs.",
    )


class ExpenseDraft(BaseModel):
    """Brouillon de dépense extrait d'une capture d'écran (Revolut) via vision.

    Sous-ensemble front-friendly d'`ExpenseCreate` : renvoyé par `POST /ask/image`
    quand le LLM lit une transaction, mais SANS rien écrire. La PWA ouvre le
    formulaire Budget pré-rempli avec ces valeurs ; l'écriture réelle passe par
    le `POST /expenses` existant après confirmation de l'utilisateur (qui peut
    cocher « Compte joint » et ajuster la catégorie).
    """

    action: str = "spend"
    amount_eur: float | None = None
    label: str | None = None
    category: str | None = None
    occurred_on: str | None = None
    shared: bool = False
    recurring_key: str | None = None


class AskResponse(BaseModel):
    response: str
    intent: str = "answer"
    refresh_cards: list[str] = Field(default_factory=list)
    expense_draft: ExpenseDraft | None = None


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
    shared: bool = False  # True → compte joint, purement informatif


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
    overdue_tasks: int
    unread_notifications: int
    budget: BudgetCard | None


# --- Location schemas -------------------------------------------------------


class LocationEventRequest(BaseModel):
    event: Literal["arrived", "left"]
    place: str = Field(min_length=1, max_length=50)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
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
    closed: bool


class ThoughtsListResponse(BaseModel):
    thoughts: list[ThoughtItem]


class ThoughtCloseResponse(BaseModel):
    closed: bool
    thought_id: int


class ThoughtCreateRequest(BaseModel):
    content: str
    kind: str | None = None  # worry|idea|note ou null


class ThoughtCreateResponse(BaseModel):
    recorded: bool
    thought: ThoughtItem
    ack: str  # accusé sobre (+ suffixe boucle si rumination détectée)


# --- Chat history schemas --------------------------------------------------


class ChatMessageItem(BaseModel):
    id: int
    role: str  # "user" | "assistant"
    content: str
    created_at: str  # ISO 8601


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessageItem]  # ordre chronologique croissant
    has_more: bool  # des bulles plus anciennes existent (curseur = messages[0].id)


# --- News schemas ----------------------------------------------------------


class NewsLatestResponse(BaseModel):
    markdown: str
    fetched_at: str  # ISO 8601 UTC


# --- For you (restitution des dépôts) schemas ------------------------------


class ForYouItemResponse(BaseModel):
    type: str  # closable_worry | loop | stale_idea
    message: str
    thought_ids: list[int]


class ForYouResponse(BaseModel):
    items: list[ForYouItemResponse]
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
    shared: bool = False  # True → compte joint, hors restant perso


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
    shared: bool = False  # True → compte joint, hors restant perso


class BudgetMonthDetail(BaseModel):
    month: str  # ISO date du début de cycle (= jour du salaire, ou 1er du mois)
    cycle_start: str  # ISO date — début du cycle budgétaire courant (inclus)
    cycle_end: str  # ISO date — dernier jour du cycle (inclus ; aujourd'hui si ouvert)
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


class CoursesShareCard(BaseModel):
    """Restant de l'enveloppe « courses », prêt à être partagé en un message.

    `text` est une phrase auto-suffisante (formatée locale FR) que le
    raccourci iOS envoie tel quel à un tiers (compagne). Les champs chiffrés
    accompagnent la phrase pour qui voudrait re-formater côté client.
    """

    text: str
    label: str
    remaining_eur: float
    allocated_eur: float
    spent_eur: float
    is_overrun: bool
    as_of: str  # ISO date du jour de calcul (fuseau serveur)


class ExpenseCreate(BaseModel):
    """Saisie budgétaire directe (formulaire PWA, sans passer par le LLM).

    Même surface que l'`intent=expense` du bot, réduite aux trois actions
    saisissables à la main : `spend` (dépense ponctuelle), `income` (revenu)
    et `tick_recurring` (pointage d'une récurrente déclarée dans le YAML).
    L'endpoint réutilise les mêmes méthodes `ExpenseManager` que
    `handle_expense_side_effect` — aucune divergence de calcul possible.
    """

    action: Literal["spend", "income", "tick_recurring"]
    # Montant en euros. Requis pour spend/income ; optionnel pour
    # tick_recurring (fallback sur le montant YAML de la récurrente).
    amount_eur: float | None = None
    label: str | None = None
    category: str | None = None
    # Date ISO YYYY-MM-DD ; None → aujourd'hui (fuseau du serveur).
    occurred_on: str | None = None
    shared: bool = False  # spend uniquement : compte joint, hors restant perso
    recurring_key: str | None = None  # tick uniquement
    # income uniquement : ancre un nouveau cycle budgétaire (= salaire reçu).
    starts_cycle: bool = False


class ExpenseCreateResponse(BaseModel):
    """Réponse de `POST /expenses`.

    `recorded=False` signale un pointage de récurrente ignoré car déjà pointé
    dans le cycle courant (idempotent, comme côté bot) ; `transaction` est
    alors `None`.
    """

    recorded: bool
    transaction: BudgetTransaction | None = None


def _expense_to_transaction(e: Expense) -> BudgetTransaction:
    """Mappe une ligne `Expense` vers le schéma de réponse `BudgetTransaction`.

    `bool(e.shared)` normalise un éventuel `None` (lignes pré-migration ou
    objet construit en mémoire sans flush DB).
    """
    return BudgetTransaction(
        id=e.id,
        kind=e.kind,
        amount_eur=e.amount_cents / 100,
        label=e.label,
        category=e.category,
        recurring_key=e.recurring_key,
        occurred_on=e.occurred_on.isoformat(),
        shared=bool(e.shared),
    )


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
    "depot": ["foryou"],
}


def _refresh_cards_for(meta: Meta) -> list[str]:
    """Retourne la liste des noms de cards à recharger après une action.

    - `intent=task` → la card tâches change ; si la due_str produit un rappel,
      la card notifications peut aussi changer (on rafraîchit les deux).
    - `intent=event` avec `action=create` → la card prochain évent change.
      `action=list` n'altère rien (lecture pure).
    - `intent=depot` → la card « Pour toi » peut gagner un candidat (boucle).
    - Autres intents → aucune card concernée, retour vide.
    """
    if meta["intent"] == "event":
        return ["next_event"] if meta["event"]["action"] == "create" else []
    return list(_REFRESH_BY_INTENT.get(meta["intent"], []))


def _expense_draft_for(meta: Meta, timezone: str) -> ExpenseDraft | None:
    """Construit un brouillon de dépense depuis le `<meta>` d'une capture image.

    Retourne None si le meta ne porte pas de dépense. La date FR (`when`) est
    résolue en ISO via `parse_when_to_date` (même logique que le chemin texte),
    pour que le formulaire Budget puisse la pré-remplir directement. Aucune
    écriture n'est faite ici : c'est le `POST /expenses` (déclenché à la
    confirmation) qui persistera la dépense.
    """
    if meta["intent"] != "expense" or not meta["expense"]["action"]:
        return None
    em = meta["expense"]
    return ExpenseDraft(
        action=em["action"] or "spend",
        amount_eur=em["amount"],
        label=em["label"],
        category=em["category"],
        occurred_on=parse_when_to_date(em["when"], timezone).isoformat(),
        shared=em["shared"],
        recurring_key=em["recurring_key"],
    )


def _sse_frame(payload: dict[str, object]) -> str:
    """Sérialise un événement en frame SSE (`data: {json}\\n\\n`)."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _format_eur_fr(value: float) -> str:
    """Formate un montant en euros, locale FR (virgule décimale).

    Les montants ronds sont affichés sans décimales (`499 €`), les autres
    avec deux décimales et une virgule (`234,50 €`). Sert aux phrases de
    partage prêtes à l'envoi (`GET /share/courses`).
    """
    rounded = round(value, 2)
    if rounded == int(rounded):
        return f"{int(rounded)} €"
    return f"{rounded:.2f}".replace(".", ",") + " €"


# Messages FR renvoyés tels quels au client iOS quand le LLM flanche. Le
# timeout est nuancé selon le canal (réponse vs analyse d'image) ; l'erreur
# serveur est commune. Centralisés ici pour rester cohérents entre `/ask`,
# `/ask/stream` et `/ask/image`.
LLM_TIMEOUT_TEXT = (
    "Le modèle met trop longtemps à répondre pour l'instant. Réessaie dans quelques secondes."
)
LLM_TIMEOUT_IMAGE_TEXT = (
    "Le modèle met trop longtemps à analyser l'image. Réessaie dans quelques secondes."
)
LLM_ERROR_TEXT = "Le modèle LLM a un souci côté serveur pour l'instant. Réessaie dans un moment."


def _llm_error_reply(exc: LLMError, *, kind: str, timeout_text: str = LLM_TIMEOUT_TEXT) -> str:
    """Log l'erreur LLM et retourne le message FR à afficher au client.

    `LLMTimeoutError` (sous-classe de `LLMError`) → message de timeout
    (`timeout_text`, paramétrable pour le cas image) ; toute autre `LLMError`
    → message d'erreur serveur. Le canal (`ask` / `stream` / `image`) est
    journalisé via `kind`.
    """
    if isinstance(exc, LLMTimeoutError):
        log.warning("llm_timeout", kind=kind)
        return timeout_text
    log.error("llm_error", kind=kind, error=str(exc))
    return LLM_ERROR_TEXT


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
    # Comparaison à temps constant : évite de fuiter la clé caractère par
    # caractère via le temps de réponse (timing attack). `compare_digest`
    # exige deux chaînes non None, d'où le fallback "" sur header absent.
    if not hmac.compare_digest(x_api_key or "", settings.api_key):
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

    # Pas de middleware CORS, volontairement : la PWA est servie par cette
    # même app (`API_BASE = ""` → appels same-origin) et les clients non
    # navigateur (Shortcuts iOS, Siri) ne sont pas soumis à CORS. Un wildcard
    # `allow_origins=["*"]` permettrait à n'importe quelle page web ouverte
    # sur un appareil du Tailnet de lire `GET /config` (et donc l'API key)
    # via un fetch cross-origin.

    # Le front React (build Vite) est l'unique interface web : servi par le
    # catch-all SPAStaticFiles monté en fin de create_app (après toutes les
    # routes explicites). Ses icônes (`/icon-1024.png`, manifest, favicon) et
    # son `index.html` (no-store) sortent directement de `frontend/dist`. Si le
    # build est absent (dev pur, CI), rien n'est monté et `/` renvoie 404.
    react_dist_available = FRONTEND_DIST.is_dir()
    if not react_dist_available:
        log.warning("frontend_dist_missing", path=str(FRONTEND_DIST))

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
        # "siri-conversation" = boucle vocale continue → mode conversation
        # (préambule dialogue multi-tours) qui implique le mode vocal.
        conversation_mode = x_source == "siri-conversation"
        voice_mode = x_source == "siri" or conversation_mode
        log.info(
            "ask_received",
            preview=payload.message[:80],
            voice_mode=voice_mode,
            conversation_mode=conversation_mode,
        )
        try:
            reply, meta = await process_message(
                payload.message,
                deps,
                voice_mode=voice_mode,
                conversation_mode=conversation_mode,
            )
        except LLMError as exc:
            return AskResponse(response=_llm_error_reply(exc, kind="ask"))
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
        "/ask/stream",
        dependencies=[Depends(verify_api_key)],
    )
    async def ask_stream(
        payload: AskRequest,
        deps: BotDeps = Depends(get_deps),
    ) -> StreamingResponse:
        """Variante streamée de `/ask` (SSE), utilisée par le mode dialogue de la PWA.

        Frames `data: {json}\\n\\n` de types `delta` / `replace` / `done` /
        `error` (cf. `bot.pipeline.StreamEvent`). Le status HTTP est figé à
        200 dès l'ouverture du stream : les erreurs LLM sont donc remontées
        en frame `error` avec les mêmes messages FR que `/ask`.
        """
        log.info("ask_stream_received", preview=payload.message[:80])

        async def event_source() -> AsyncIterator[str]:
            try:
                async for event in process_message_stream(payload.message, deps):
                    if event["type"] == "done":
                        meta = event["meta"]
                        yield _sse_frame(
                            {
                                "type": "done",
                                "intent": meta["intent"],
                                "refresh_cards": _refresh_cards_for(meta),
                            }
                        )
                    else:
                        yield _sse_frame({"type": event["type"], "text": event.get("text", "")})
            except LLMError as exc:
                yield _sse_frame({"type": "error", "text": _llm_error_reply(exc, kind="stream")})
            except Exception as exc:
                log.exception("ask_stream_failed", error=str(exc))
                capture_exception(exc, source="api_ask_stream")
                yield _sse_frame(
                    {
                        "type": "error",
                        "text": "Une erreur interne est survenue. Réessaie dans un moment.",
                    }
                )

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={
                # no-store : jamais de cache sur un stream de conversation.
                # X-Accel-Buffering : neutralise le buffering d'un éventuel
                # reverse proxy (nginx) entre Tailscale et uvicorn.
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
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
        except LLMError as exc:
            return AskResponse(
                response=_llm_error_reply(exc, kind="image", timeout_text=LLM_TIMEOUT_IMAGE_TEXT)
            )
        except Exception as exc:
            log.exception("ask_image_failed", error=str(exc))
            capture_exception(exc, source="api_ask_image")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal error",
            ) from exc
        draft = _expense_draft_for(meta, deps.settings.timezone)
        if draft is not None:
            # Dépense lue depuis la capture : rien n'a été écrit (cf. pipeline).
            # On renvoie le brouillon pour pré-remplir le formulaire Budget et
            # on laisse refresh_cards vide tant que l'utilisateur n'a pas validé.
            return AskResponse(
                response=reply, intent=meta["intent"], refresh_cards=[], expense_draft=draft
            )
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
        "/foryou",
        response_model=ForYouResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def foryou(deps: BotDeps = Depends(get_deps)) -> ForYouResponse:
        """Card "Pour toi" : restitution des dépôts (clôture, boucle, idée ancienne).

        Canal 100 % pull, fetch au tap. L'orchestrateur est fail-soft : une
        dépendance externe down dégrade la card sans jamais la faire échouer.
        Un garde-fou supplémentaire ici garantit qu'aucune exception inattendue
        ne remonte en 500 — au pire la card est vide.
        """
        try:
            result = await deps.foryou.build()
        except Exception as exc:  # défense en profondeur : la card ne 500 jamais
            log.exception("foryou_failed", error=str(exc))
            capture_exception(exc, source="api_foryou")
            return ForYouResponse(items=[], fetched_at=datetime.now(UTC).isoformat())

        return ForYouResponse(
            items=[
                ForYouItemResponse(
                    type=item.type,
                    message=item.message,
                    thought_ids=list(item.thought_ids),
                )
                for item in result.items
            ],
            fetched_at=result.fetched_at.isoformat(),
        )

    @app.post(
        "/thoughts",
        response_model=ThoughtCreateResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def create_thought(
        body: ThoughtCreateRequest,
        deps: BotDeps = Depends(get_deps),
    ) -> ThoughtCreateResponse:
        """Dépôt cognitif direct (card "Dépôt express" du dashboard, sans LLM).

        Canal parallèle à l'`intent=depot` du bot : réutilise `record_depot`
        (mêmes écritures SQLite + ChromaDB + détection de boucle) pour garantir
        zéro divergence. L'accusé est sobre et reprend la même formulation de
        boucle que le chemin bot (`loop_suffix`).
        """
        content = body.content.strip()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="`content` ne doit pas être vide",
            )
        if body.kind is not None and body.kind not in VALID_DEPOT_KINDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"`kind` invalide : {body.kind!r} (worry|idea|note ou null)",
            )
        thought, loop_size = await record_depot(content=content, kind=body.kind, deps=deps)
        ack = "C'est posé." + (loop_suffix(loop_size) if loop_size is not None else "")
        return ThoughtCreateResponse(
            recorded=True,
            thought=ThoughtItem(
                id=thought.id,
                content=thought.content,
                kind=thought.kind,
                created_at=thought.created_at.isoformat(),
                closed=thought.processed_at is not None,
            ),
            ack=ack,
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
        kind: str | None = None,
    ) -> ThoughtsListResponse:
        """Liste les dépôts cognitifs récents (intent `depot`).

        Filtre optionnel `since` (ISO 8601) et `kind` (`worry|idea|note`).
        `limit` plafonné à 200 (clamp tolérant plutôt que rejet) pour éviter
        les payloads trop gros. Tri chronologique inverse (les dépôts les plus
        récents en premier).
        """
        if kind is not None and kind not in VALID_DEPOT_KINDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"`kind` doit valoir {sorted(VALID_DEPOT_KINDS)}, reçu {kind!r}",
            )
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
            rows = await deps.thoughts.list_recent(limit=capped_limit, kind=kind)

        items = [
            ThoughtItem(
                id=t.id,
                content=t.content,
                kind=t.kind,
                created_at=t.created_at.isoformat(),
                closed=t.processed_at is not None,
            )
            for t in rows
        ]
        return ThoughtsListResponse(thoughts=items)

    @app.post(
        "/thoughts/{thought_id}/close",
        response_model=ThoughtCloseResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def close_thought(
        thought_id: int, deps: BotDeps = Depends(get_deps)
    ) -> ThoughtCloseResponse:
        """Clôt un dépôt cognitif (tap PWA, card "Pour toi").

        Idempotent : re-clore un dépôt déjà clos renvoie 200 sans modifier
        le `processed_at` initial. 404 si l'id est inconnu.
        """
        ok = await deps.thoughts.close(thought_id)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thought not found",
            )
        return ThoughtCloseResponse(closed=True, thought_id=thought_id)

    @app.get(
        "/history",
        response_model=ChatHistoryResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def chat_history(
        deps: BotDeps = Depends(get_deps),
        limit: int = 50,
        before_id: int | None = None,
    ) -> ChatHistoryResponse:
        """Historique des bulles du mode dialogue (réaffichage PWA).

        Renvoie les `limit` derniers messages (plafonné à 200), ordre
        chronologique croissant. Scroll infini : passer `before_id` =
        `messages[0].id` de la page courante pour récupérer les plus anciens.
        `has_more` signale qu'il en reste à charger. Si la persistance est
        désactivée (pas de `chat_history`), renvoie une page vide.
        """
        if deps.chat_history is None:
            return ChatHistoryResponse(messages=[], has_more=False)
        page = await deps.chat_history.page(limit=limit, before_id=before_id)
        items = [
            ChatMessageItem(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at.isoformat(),
            )
            for m in page.messages
        ]
        return ChatHistoryResponse(messages=items, has_more=page.has_more)

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
        from datetime import timedelta

        from bot.finance.budget import compute_budget
        from bot.finance.config import extract_finance_config
        from bot.finance.manager import OPEN_CYCLE_END

        tz = ZoneInfo(deps.settings.timezone)
        today_d = datetime.now(tz).date()
        cfg = extract_finance_config(deps.profile.data)
        cycle_start, cycle_end = await deps.expenses.current_cycle_bounds(today_d)
        month_rows = await deps.expenses.list_for_cycle(today_d)
        year_savings = await deps.expenses.list_savings_for_year(today_d.year)
        summary = compute_budget(
            config=cfg,
            month_expenses=month_rows,
            year_savings=year_savings,
            today=today_d,
            cycle_start=cycle_start,
            cycle_end=cycle_end,
        )
        # Borne haute affichable / exportable (inclusive) : la veille de la
        # prochaine ancre, ou aujourd'hui tant que le cycle est ouvert.
        if cycle_end >= OPEN_CYCLE_END:
            cycle_end_inclusive = max(cycle_start, today_d)
        else:
            cycle_end_inclusive = cycle_end - timedelta(days=1)

        transactions = [_expense_to_transaction(e) for e in month_rows]
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
                shared=env.shared,
            )
            for env in summary.envelopes
        ]
        return BudgetMonthDetail(
            month=summary.month.isoformat(),
            cycle_start=cycle_start.isoformat(),
            cycle_end=cycle_end_inclusive.isoformat(),
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
        "/share/courses",
        response_model=CoursesShareCard,
        dependencies=[Depends(verify_api_key)],
    )
    async def share_courses(
        deps: BotDeps = Depends(get_deps),
    ) -> CoursesShareCard:
        """Restant de l'enveloppe « courses », formaté pour partage direct.

        Pensé pour un raccourci iOS : il fetch cette route puis pousse `text`
        dans la feuille de partage (Messages/WhatsApp) vers un tiers. Le
        calcul réutilise `compute_budget` (mêmes invariants que le dashboard).

        L'enveloppe ciblée est la première dont la catégorie OU le label
        contient « cours » (insensible à la casse) — robuste que la course
        soit déclarée `category: courses` ou `label: "Courses (compte joint)"`.
        404 si aucune enveloppe de ce type n'est configurée dans le YAML.
        """
        from bot.finance.budget import compute_budget
        from bot.finance.config import extract_finance_config

        tz = ZoneInfo(deps.settings.timezone)
        today_d = datetime.now(tz).date()
        cfg = extract_finance_config(deps.profile.data)
        cycle_start, cycle_end = await deps.expenses.current_cycle_bounds(today_d)
        month_rows = await deps.expenses.list_for_cycle(today_d)
        year_savings = await deps.expenses.list_savings_for_year(today_d.year)
        summary = compute_budget(
            config=cfg,
            month_expenses=month_rows,
            year_savings=year_savings,
            today=today_d,
            cycle_start=cycle_start,
            cycle_end=cycle_end,
        )

        env = next(
            (
                e
                for e in summary.envelopes
                if "cours" in e.category.lower() or "cours" in e.label.lower()
            ),
            None,
        )
        if env is None:
            raise HTTPException(
                status_code=404,
                detail="Aucune enveloppe 'courses' configurée dans finances.envelopes",
            )

        remaining_eur = env.remaining_cents / 100
        allocated_eur = env.allocated_cents / 100
        spent_eur = env.spent_cents / 100
        as_of_fr = today_d.strftime("%d/%m")
        if env.is_overrun:
            text = (
                f"Courses : enveloppe dépassée de {_format_eur_fr(-remaining_eur)} "
                f"({_format_eur_fr(spent_eur)} dépensés sur {_format_eur_fr(allocated_eur)}, "
                f"au {as_of_fr})"
            )
        else:
            text = (
                f"Courses : il reste {_format_eur_fr(remaining_eur)} "
                f"sur {_format_eur_fr(allocated_eur)} (au {as_of_fr})"
            )

        return CoursesShareCard(
            text=text,
            label=env.label,
            remaining_eur=remaining_eur,
            allocated_eur=allocated_eur,
            spent_eur=spent_eur,
            is_overrun=env.is_overrun,
            as_of=today_d.isoformat(),
        )

    @app.post(
        "/expenses",
        response_model=ExpenseCreateResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def create_expense(
        body: ExpenseCreate,
        deps: BotDeps = Depends(get_deps),
    ) -> ExpenseCreateResponse:
        """Saisie budgétaire directe par formulaire (sans LLM).

        Équivalent transport de `handle_expense_side_effect` : réutilise les
        mêmes méthodes `ExpenseManager` que le chemin bot, donc les deux vues
        (formulaire PWA + `intent=expense`) partagent strictement la même
        persistance et le même calcul de cycle.
        """
        from bot.finance.config import extract_finance_config
        from bot.pipeline.side_effects import euros_to_cents

        # « Aujourd'hui » calculé comme dans get_budget (fuseau du serveur).
        tz = ZoneInfo(deps.settings.timezone)
        if body.occurred_on is None:
            occurred_on = datetime.now(tz).date()
        else:
            try:
                occurred_on = date.fromisoformat(body.occurred_on)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Date invalide (attendu YYYY-MM-DD)",
                ) from exc

        override_cents = euros_to_cents(body.amount_eur)

        if body.action == "spend":
            if override_cents is None:
                raise HTTPException(status_code=400, detail="Montant requis et > 0")
            label = (body.label or "").strip() or "Dépense"
            category = (body.category or "").strip() or None
            expense = await deps.expenses.add_punctual(
                amount_cents=override_cents,
                label=label,
                category=category,
                occurred_on=occurred_on,
                shared=body.shared,
            )
            log.info(
                "expense_form_spend_recorded",
                expense_id=expense.id,
                amount_cents=override_cents,
                shared=body.shared,
            )
            return ExpenseCreateResponse(
                recorded=True, transaction=_expense_to_transaction(expense)
            )

        if body.action == "income":
            if body.starts_cycle:
                await deps.expenses.start_cycle(occurred_on)
            if override_cents is None:
                raise HTTPException(status_code=400, detail="Montant requis et > 0")
            label = (body.label or "").strip() or "Revenu"
            expense = await deps.expenses.add_income(
                amount_cents=override_cents,
                label=label,
                occurred_on=occurred_on,
            )
            log.info(
                "expense_form_income_recorded",
                expense_id=expense.id,
                amount_cents=override_cents,
                starts_cycle=body.starts_cycle,
            )
            return ExpenseCreateResponse(
                recorded=True, transaction=_expense_to_transaction(expense)
            )

        # action == "tick_recurring"
        key = (body.recurring_key or "").strip()
        if not key:
            raise HTTPException(status_code=400, detail="recurring_key requis")
        cfg = extract_finance_config(deps.profile.data)
        item = cfg.find(key)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Récurrente inconnue : {key}")
        # Montant : override euros si fourni, sinon montant YAML de la récurrente.
        amount_cents = override_cents if override_cents is not None else item.amount_cents
        tick = await deps.expenses.tick_recurring_once(
            recurring_key=item.key,
            label=item.label,
            amount_cents=amount_cents,
            kind=item.kind,
            occurred_on=occurred_on,
            category=item.category,
        )
        if tick is None:
            log.info("expense_form_tick_duplicate_ignored", key=item.key)
            return ExpenseCreateResponse(recorded=False, transaction=None)
        log.info(
            "expense_form_recurring_ticked",
            expense_id=tick.id,
            key=item.key,
            amount_cents=amount_cents,
        )
        return ExpenseCreateResponse(recorded=True, transaction=_expense_to_transaction(tick))

    @app.get(
        "/expenses/export.csv",
        response_class=Response,
        dependencies=[Depends(verify_api_key)],
        include_in_schema=False,
    )
    async def export_expenses_csv(
        deps: BotDeps = Depends(get_deps),
        from_: str = Query(..., alias="from", description="Date de début (YYYY-MM-DD, inclusif)"),
        to: str = Query(..., description="Date de fin (YYYY-MM-DD, inclusif)"),
    ) -> Response:
        """Exporte les écritures budgétaires (`expenses`) entre `from` et `to`.

        Format CSV pensé pour s'ouvrir directement dans Numbers/Excel locale
        FR : séparateur `;`, virgule décimale, dates `JJ/MM/AAAA`, UTF-8 avec
        BOM. Les 4 kinds sont inclus, distinguables via la colonne `type`.
        Le montant est signé (`income` positif, autres négatifs) pour
        autoriser une `SOMME` directe côté tableur.
        """
        try:
            start = date.fromisoformat(from_)
            end = date.fromisoformat(to)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Dates invalides (attendu YYYY-MM-DD)",
            ) from exc
        if start > end:
            raise HTTPException(
                status_code=400,
                detail="`from` doit être <= `to`",
            )

        rows = await deps.expenses.list_between(start, end)
        body = build_expenses_csv(rows)
        filename = f"copain-depenses-{start.isoformat()}_{end.isoformat()}.csv"
        return Response(
            content=body.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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

    # Catch-all SPA : monté APRÈS toutes les routes explicites (elles restent
    # prioritaires) et uniquement si le build React existe. Sert index.html sur
    # `/`, les assets hashés, les icônes/manifest, et retombe sur index.html
    # pour tout chemin inconnu (routing client).
    if react_dist_available:
        app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST, html=True), name="spa")

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
                    shared=env.shared,
                )
                for env in b.envelopes
            ],
            has_envelope_overrun=b.has_envelope_overrun,
        )
    return DashboardResponse(
        weather=weather,
        next_event=next_event,
        today_tasks=tasks,
        overdue_tasks=snap.overdue_tasks_count,
        unread_notifications=snap.unread_notifications,
        budget=budget,
    )
