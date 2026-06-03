"""Orchestration de la restitution des dépôts cognitifs (card "Pour toi").

Couche I/O fail-soft (pattern `NewsCurator` / `bot/dashboard.py`) qui :

1. collecte les dépôts ouverts (SQLite), les évènements calendrier passés et
   les boucles de rumination (similarité ChromaDB) ;
2. passe ces faits aux heuristiques **pures** de `bot.thoughts.restitution`
   (le *quoi*, déterministe) ;
3. fait formuler les items retenus par **un seul** appel LLM (le *comment*),
   avec repli sur des messages template si le LLM est indisponible ;
4. tamponne le cooldown (`surfaced_at`) des items restitués.

Chaque dépendance externe est isolée sous `try/except` : calendrier down →
`closable_worry` sur l'ancienneté seule, ChromaDB down → pas de boucle,
LLM down → formulation template. L'endpoint `GET /foryou` ne renvoie jamais
de 500 à cause d'une de ces pannes (canal 100 % pull, zéro notification).

Le rapprochement worry ↔ évent passé est **lexical** (tokens significatifs
partagés) et non sémantique : l'`Embedder` n'est pas exposé dans `BotDeps`
et ré-embedder les titres d'évents à chaque tap serait coûteux sur le Pi.
L'ancienneté (> 14 j) reste le déclencheur principal de `closable_worry` ;
le lexical n'en est qu'un booster de précision.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bot.calendar.client import ICloudCalendarError
from bot.logging_conf import get_logger
from bot.thoughts.restitution import (
    CLOSABLE_EVENT_WINDOW_DAYS,
    LOOP_MIN_MEMBERS,
    LOOP_WINDOW_DAYS,
    Candidate,
    LoopFacts,
    ThoughtFacts,
    select_candidates,
)

if TYPE_CHECKING:
    from bot.calendar.client import ICloudCalendarClient
    from bot.calendar.models import CalendarEvent
    from bot.llm.client import LLMClient
    from bot.memory.manager import MemoryManager
    from bot.thoughts.manager import ThoughtManager
    from bot.thoughts.models import Thought

log = get_logger(__name__)

# Mots vides FR ignorés dans le rapprochement lexical worry ↔ évent (en plus
# du filtre de longueur). Liste volontairement courte : on cherche surtout à
# écarter les mots-outils fréquents qui produiraient de faux rapprochements.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "pour",
        "avec",
        "dans",
        "mais",
        "donc",
        "elle",
        "leur",
        "leurs",
        "vous",
        "nous",
        "mon",
        "mes",
        "tes",
        "ses",
        "ces",
        "des",
        "les",
        "une",
        "est",
        "sont",
        "plus",
        "tres",
        "cette",
        "que",
        "qui",
        "quoi",
        "sur",
        "sous",
        "chez",
        "celui",
        "celle",
        "alors",
        "comme",
    }
)
_MIN_TOKEN_LEN = 4


@dataclass(frozen=True, slots=True)
class ForYouItem:
    """Item de restitution prêt pour la PWA : type, message formulé, ids ciblés."""

    type: str  # CandidateType : closable_worry | loop | stale_idea
    message: str
    thought_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ForYouResult:
    """Réponse de `GET /foryou` : items (≤ 2) + horodatage du fetch."""

    items: list[ForYouItem]
    fetched_at: datetime


class ForYouBuilder:
    """Assemble la card "Pour toi" : collecte I/O → heuristiques → formulation."""

    def __init__(
        self,
        thoughts: ThoughtManager,
        memory: MemoryManager,
        calendar: ICloudCalendarClient,
        llm: LLMClient,
        *,
        similarity_max_distance: float,
        max_seeds: int = 20,
        top_k: int = 8,
    ) -> None:
        self._thoughts = thoughts
        self._memory = memory
        self._calendar = calendar
        self._llm = llm
        self._max_distance = similarity_max_distance
        self._max_seeds = max_seeds
        self._top_k = top_k

    async def build(self, *, now: datetime | None = None) -> ForYouResult:
        """Construit la card. Ne lève jamais à cause d'une panne externe."""
        now = now or datetime.now(UTC)

        open_rows = await self._safe_list_open()
        open_facts = [_to_facts(r) for r in open_rows]
        if not open_facts:
            log.info("foryou_built", count=0, types=[])
            return ForYouResult(items=[], fetched_at=now)

        facts_by_id = await self._window_facts(now)
        for fact in open_facts:
            facts_by_id.setdefault(fact.thought_id, fact)

        events = await self._safe_past_events(now)
        worries = [f for f in open_facts if f.kind == "worry"]
        event_matched = match_worries_to_events(worries, events)

        loops = await self._detect_loops(open_facts, facts_by_id)

        candidates = select_candidates(
            thoughts=open_facts,
            loops=loops,
            event_matched_worries=event_matched,
            now=now,
        )

        items = await self._formulate(candidates)
        surfaced_ids = [tid for c in candidates for tid in c.thought_ids]
        if surfaced_ids:
            await self._safe_mark_surfaced(surfaced_ids)

        log.info("foryou_built", count=len(items), types=[it.type for it in items])
        return ForYouResult(items=items, fetched_at=now)

    # --- collectes I/O fail-soft -------------------------------------------

    async def _safe_list_open(self) -> Sequence[Thought]:
        try:
            return await self._thoughts.list_open(kinds=None, limit=self._max_seeds)
        except Exception as exc:  # SQLite down → card vide plutôt qu'un 500
            log.warning("foryou_list_open_failed", error=str(exc))
            return []

    async def _window_facts(self, now: datetime) -> dict[int, ThoughtFacts]:
        """Dépôts (ouverts ET clos) de la fenêtre de boucle, indexés par id.

        Sert à reconstruire les membres d'une boucle : le comptage se fait sur
        `created_at` indépendamment de `processed_at`. La borne `since` est
        passée **naïve** (comparaison SQL côté SQLite naïf).
        """
        since = (now - timedelta(days=LOOP_WINDOW_DAYS)).replace(tzinfo=None)
        try:
            rows = await self._thoughts.list_since(since)
        except Exception as exc:
            log.warning("foryou_list_since_failed", error=str(exc))
            return {}
        return {r.id: _to_facts(r) for r in rows}

    async def _safe_past_events(self, now: datetime) -> list[CalendarEvent]:
        if not self._calendar.is_connected:
            return []
        try:
            start = now - timedelta(days=CLOSABLE_EVENT_WINDOW_DAYS)
            return await self._calendar.list_all_between(start, now)
        except ICloudCalendarError as exc:
            log.warning("foryou_calendar_skipped", error=str(exc))
            return []

    async def _detect_loops(
        self,
        open_facts: Sequence[ThoughtFacts],
        facts_by_id: Mapping[int, ThoughtFacts],
    ) -> list[LoopFacts]:
        """Regroupe les dépôts similaires en boucles, dédupliquées par chevauchement.

        Les notes sont exclues (graine comme membre). Une panne ChromaDB →
        aucune boucle (les autres candidats restent calculés). Deux graines qui
        partagent un membre désignent la même rumination → on n'en garde qu'une.
        """
        seeds = sorted(
            (f for f in open_facts if f.kind != "note"),
            key=lambda f: f.created_at,
            reverse=True,
        )
        loops: list[LoopFacts] = []
        kept_ids: set[int] = set()
        for seed in seeds:
            try:
                matches = await self._memory.find_similar_depots(
                    seed.content, top_k=self._top_k, max_distance=self._max_distance
                )
            except Exception as exc:  # ChromaDB down → pas de boucles du tout
                log.warning("foryou_loop_lookup_failed", error=str(exc))
                return []
            member_ids = {seed.thought_id}
            for match in matches:
                member = facts_by_id.get(match.thought_id)
                if member is not None and member.kind != "note":
                    member_ids.add(match.thought_id)
            if len(member_ids) < LOOP_MIN_MEMBERS:
                continue
            if member_ids & kept_ids:
                continue
            loops.append(LoopFacts(members=tuple(facts_by_id[i] for i in member_ids)))
            kept_ids |= member_ids
        return loops

    async def _safe_mark_surfaced(self, ids: Sequence[int]) -> None:
        try:
            await self._thoughts.mark_surfaced(ids)
        except Exception as exc:
            log.warning("foryou_mark_surfaced_failed", error=str(exc))

    # --- formulation LLM (un seul appel, repli template) -------------------

    async def _formulate(self, candidates: Sequence[Candidate]) -> list[ForYouItem]:
        if not candidates:
            return []
        messages = await self._formulate_messages(candidates)
        return [
            ForYouItem(type=c.type, message=messages[i], thought_ids=tuple(c.thought_ids))
            for i, c in enumerate(candidates)
        ]

    async def _formulate_messages(self, candidates: Sequence[Candidate]) -> list[str]:
        templates = [_template(c) for c in candidates]
        try:
            raw = await self._llm.chat(
                messages=[
                    {"role": "system", "content": _FORMULATION_SYSTEM},
                    {"role": "user", "content": _serialize_candidates(candidates)},
                ],
                cacheable=False,
            )
        except Exception as exc:  # LLM down → on garde les templates
            log.warning("foryou_llm_failed", error=str(exc))
            return templates

        parsed = _parse_messages(raw)
        if parsed is None:
            log.warning("foryou_llm_unparsable", preview=raw[:120])
            return templates
        return [
            parsed[i].strip()
            if i < len(parsed) and isinstance(parsed[i], str) and parsed[i].strip()
            else templates[i]
            for i in range(len(candidates))
        ]


# --- helpers purs -----------------------------------------------------------


def match_worries_to_events(
    worries: Iterable[Any],
    events: Iterable[Any],
) -> dict[int, str]:
    """Rapproche chaque souci d'un évent passé par tokens significatifs partagés.

    Retourne `{thought_id: titre de l'évent}` pour le premier évent dont le
    titre partage au moins un token significatif (longueur ≥ 4, hors mots
    vides) avec le contenu du souci. Rapprochement purement lexical, sans I/O.
    """
    event_tokens = [(e.title, _significant_tokens(e.title)) for e in events]
    matched: dict[int, str] = {}
    for worry in worries:
        worry_tokens = _significant_tokens(worry.content)
        if not worry_tokens:
            continue
        for title, tokens in event_tokens:
            if worry_tokens & tokens:
                matched[worry.id] = title
                break
    return matched


def _significant_tokens(text: str) -> set[str]:
    """Tokens normalisés (sans accents, minuscule), longs et non vides de sens."""
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(c for c in normalized if not unicodedata.combining(c))
    raw = "".join(c if c.isalnum() else " " for c in ascii_text).split()
    return {tok for tok in raw if len(tok) >= _MIN_TOKEN_LEN and tok not in _STOPWORDS}


def _to_facts(row: Any) -> ThoughtFacts:
    """Convertit une ligne SQLite en `ThoughtFacts` (datetimes réattachées UTC)."""
    return ThoughtFacts(
        thought_id=row.id,
        kind=row.kind,
        created_at=_aware(row.created_at),
        surfaced_at=_aware(row.surfaced_at),
        is_open=row.processed_at is None,
        content=row.content,
    )


def _aware(value: datetime | None) -> Any:
    """Réattache UTC à une datetime SQLite naïve (les heuristiques l'exigent aware)."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _template(candidate: Candidate) -> str:
    """Message de repli sobre par type (utilisé si le LLM est indisponible)."""
    content = candidate.content.strip()
    if candidate.type == "loop":
        suffix = f" ({candidate.context})" if candidate.context else ""
        return f"Ça revient souvent ces temps-ci : « {content} »{suffix}."
    if candidate.type == "stale_idea":
        return (
            f"Une idée déposée il y a un moment : « {content} ». Tu veux en faire quelque chose ?"
        )
    if candidate.context:
        return (
            f"Tu avais noté « {content} » — c'est peut-être réglé depuis « {candidate.context} » ?"
        )
    return f"Ça fait un moment que tu portes « {content} ». Toujours d'actualité ?"


def _serialize_candidates(candidates: Sequence[Candidate]) -> str:
    """Sérialise les candidats pour le prompt de formulation (un objet par item)."""
    payload = [
        {
            "index": i,
            "type": c.type,
            "contenu": c.content,
            "contexte": c.context,
        }
        for i, c in enumerate(candidates)
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_messages(raw: str) -> list[Any] | None:
    """Extrait un tableau JSON de la réponse LLM, sinon None (→ repli template)."""
    stripped = raw.strip()
    for candidate in (stripped, _extract_array(stripped)):
        if candidate is None:
            continue
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, list):
            return data
    return None


def _extract_array(raw: str) -> str | None:
    """Isole le premier `[ … ]` d'une réponse bavarde (LLM qui enrobe le JSON)."""
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end <= start:
        return None
    return raw[start : end + 1]


_FORMULATION_SYSTEM = (
    "Tu es copain, un cerveau d'appoint pour Arnaud (TDA/H + anxiété). On te "
    "donne une liste d'items de restitution déjà sélectionnés (tu ne choisis "
    "PAS quoi montrer, seulement comment le dire).\n\n"
    "Pour CHAQUE item, rédige UNE phrase courte, sobre et bienveillante en "
    "français, qui aide Arnaud à sortir la chose de sa tête sans rien y "
    "rajouter :\n"
    "- closable_worry : suggère doucement que ce souci est peut-être réglé "
    "(propose, n'affirme jamais).\n"
    "- loop : objective sans juger qu'un sujet revient souvent ces temps-ci.\n"
    "- stale_idea : fais resurgir l'idée sans pression.\n\n"
    "Réponds UNIQUEMENT par un tableau JSON de chaînes, une par item, dans "
    "l'ordre reçu (champ `index`). Pas de markdown, pas de bloc <meta>, pas "
    'de texte autour. Exemple : ["…", "…"]'
)
