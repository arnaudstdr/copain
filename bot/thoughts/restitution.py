"""Heuristiques de restitution des dépôts cognitifs (card "Pour toi").

Pure functions, sans I/O (pattern `bot/finance/budget.py`). Entrées : des
faits déjà collectés par l'orchestration (dépôts SQLite, boucles issues de
la similarité ChromaDB, rapprochements worry ↔ évent passé). Sortie : les
candidats de restitution priorisés, plafonnés à `MAX_ITEMS`.

Les heuristiques décident du *quoi* (déterministe, testable) ; le LLM ne
fait que le *comment* (formulation, step 06). Toutes les datetimes reçues
doivent être **aware** — la réattache UTC des valeurs SQLite naïves est la
responsabilité de l'orchestration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bot.memory.manager import DepotMatch

LOOP_WINDOW_DAYS = 30
LOOP_MIN_MEMBERS = 3
CLOSABLE_EVENT_WINDOW_DAYS = 7
STALE_WORRY_DAYS = 14
STALE_IDEA_DAYS = 14
SURFACED_COOLDOWN_DAYS = 7
MAX_ITEMS = 2

CandidateType = Literal["closable_worry", "loop", "connection", "stale_idea"]
# Nature du `context` d'un `closable_worry` : évènement passé rapproché ou état
# de budget sain. Pilote la formulation (step 06) sans multiplier les types
# côté PWA (un souci apaisé par le budget reste un `closable_worry`).
ContextKind = Literal["event", "budget"]

# Défaut immutable partagé (évite un littéral mutable en signature).
_NO_BUDGET_REASSURANCE: Mapping[int, str] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class ThoughtFacts:
    """Faits d'un dépôt, déjà extraits de SQLite (datetimes aware)."""

    thought_id: int
    kind: str | None
    created_at: datetime
    surfaced_at: datetime | None
    is_open: bool
    content: str


@dataclass(frozen=True, slots=True)
class LoopFacts:
    """Membres d'une boucle de rumination détectée par similarité.

    Inclut les membres clos : le comptage se fait sur `created_at`,
    indépendamment de `processed_at`.
    """

    members: tuple[ThoughtFacts, ...]


@dataclass(frozen=True, slots=True)
class ConnectionFacts:
    """Deux dépôts sémantiquement proches sans former de boucle (< 3 membres).

    Versant *fertile* du même signal de proximité que la boucle (versant
    *anxieux*) : `a` est la graine (dépôt ouvert récent), `b` le voisin le
    plus proche relié. `distance` cosine (plus petit = plus fort).
    """

    a: ThoughtFacts
    b: ThoughtFacts
    distance: float


@dataclass(frozen=True, slots=True)
class Candidate:
    """Candidat de restitution retenu, prêt pour la formulation LLM."""

    type: CandidateType
    thought_ids: tuple[int, ...]
    content: str  # matière pour la formulation LLM (step 06)
    context: str | None  # ex. titre de l'évent passé rapproché, ou dépôt relié
    context_kind: ContextKind | None = None  # nature du context (event | budget)


def select_candidates(
    *,
    thoughts: Sequence[ThoughtFacts],
    loops: Sequence[LoopFacts],
    event_matched_worries: Mapping[int, str],
    now: datetime,
    connections: Sequence[ConnectionFacts] = (),
    budget_reassured_worries: Mapping[int, str] = _NO_BUDGET_REASSURANCE,
) -> list[Candidate]:
    """Applique règles, cooldown, priorité et plafond.

    `thoughts` peut contenir tous kinds et états : chaque règle filtre
    strictement. `event_matched_worries` mappe `thought_id` → titre de
    l'évent calendrier passé rapproché (calculé en amont, similarité = I/O).
    `budget_reassured_worries` mappe `thought_id` → phrase d'état de budget
    sain (calculée en amont, fail-soft) pour un souci d'argent apaisable ;
    vide si le budget est tendu (on ne rassure jamais à tort).

    Priorité (décroissante) : closable_worry > loop > connection > stale_idea.
    Un `thought_id` déjà restitué par un candidat prioritaire exclut tout
    candidat moins prioritaire qui le contient (dédup inter-types). C'est ce
    qui règle la collision boucle/connexion : une connexion partageant un id
    avec une boucle retenue est automatiquement écartée.
    """
    ordered = [
        *_closable_worries(thoughts, event_matched_worries, budget_reassured_worries, now),
        *_loop_candidates(loops, now),
        *_connection_candidates(connections, now),
        *_stale_ideas(thoughts, now),
    ]
    selected: list[Candidate] = []
    seen_ids: set[int] = set()
    for candidate in ordered:
        if len(selected) >= MAX_ITEMS:
            break
        if seen_ids.intersection(candidate.thought_ids):
            continue
        selected.append(candidate)
        seen_ids.update(candidate.thought_ids)
    return selected


def _closable_worries(
    thoughts: Sequence[ThoughtFacts],
    event_matched_worries: Mapping[int, str],
    budget_reassured_worries: Mapping[int, str],
    now: datetime,
) -> list[Candidate]:
    """Soucis ouverts rapprochés d'un évent, apaisés par le budget, OU > 14 j.

    `context`/`context_kind` retiennent le signal le plus spécifique : évent
    passé d'abord, puis budget sain, sinon rien (déclenchement par ancienneté).
    """
    eligible = [
        t
        for t in thoughts
        if t.is_open
        and t.kind == "worry"
        and not _in_cooldown(t.surfaced_at, now)
        and (
            t.thought_id in event_matched_worries
            or t.thought_id in budget_reassured_worries
            or _older_than(t.created_at, now, STALE_WORRY_DAYS)
        )
    ]
    eligible.sort(key=lambda t: t.created_at, reverse=True)
    candidates: list[Candidate] = []
    for t in eligible:
        context: str | None
        context_kind: ContextKind | None
        if t.thought_id in event_matched_worries:
            context, context_kind = event_matched_worries[t.thought_id], "event"
        elif t.thought_id in budget_reassured_worries:
            context, context_kind = budget_reassured_worries[t.thought_id], "budget"
        else:
            context, context_kind = None, None
        candidates.append(
            Candidate(
                type="closable_worry",
                thought_ids=(t.thought_id,),
                content=t.content,
                context=context,
                context_kind=context_kind,
            )
        )
    return candidates


def _loop_candidates(loops: Sequence[LoopFacts], now: datetime) -> list[Candidate]:
    """Boucles d'au moins LOOP_MIN_MEMBERS membres sur la fenêtre, ≥ 1 ouvert."""
    out: list[tuple[datetime, Candidate]] = []
    for loop in loops:
        members = [m for m in loop.members if _within(m.created_at, now, LOOP_WINDOW_DAYS)]
        if len(members) < LOOP_MIN_MEMBERS:
            continue
        if not any(m.is_open for m in members):
            continue
        last_surfaced = max(
            (m.surfaced_at for m in members if m.surfaced_at is not None), default=None
        )
        newest = max(members, key=lambda m: m.created_at)
        if (
            _in_cooldown(last_surfaced, now)
            and last_surfaced is not None
            and newest.created_at <= last_surfaced
        ):
            continue  # restituée récemment et aucun nouveau membre depuis
        out.append(
            (
                newest.created_at,
                Candidate(
                    type="loop",
                    thought_ids=tuple(m.thought_id for m in members),
                    content=newest.content,
                    context=f"{len(members)} dépôts en {LOOP_WINDOW_DAYS} jours",
                ),
            )
        )
    out.sort(key=lambda pair: pair[0], reverse=True)
    return [candidate for _, candidate in out]


def _connection_candidates(
    connections: Sequence[ConnectionFacts], now: datetime
) -> list[Candidate]:
    """Paires de dépôts reliés, ni l'un ni l'autre restitué récemment.

    Trié par distance croissante (lien le plus fort d'abord). Le cooldown est
    respecté sur les DEUX membres : si l'un des deux a déjà été ressorti il y a
    moins de SURFACED_COOLDOWN_DAYS, la connexion attend. `content` porte la
    graine (dépôt récent), `context` l'autre dépôt relié (matière LLM).
    """
    eligible = [
        c
        for c in connections
        if not _in_cooldown(c.a.surfaced_at, now) and not _in_cooldown(c.b.surfaced_at, now)
    ]
    eligible.sort(key=lambda c: c.distance)
    return [
        Candidate(
            type="connection",
            thought_ids=(c.a.thought_id, c.b.thought_id),
            content=c.a.content,
            context=c.b.content,
        )
        for c in eligible
    ]


def _stale_ideas(thoughts: Sequence[ThoughtFacts], now: datetime) -> list[Candidate]:
    """Idées ouvertes > 14 j, jamais restituées ou restituées il y a > 14 j."""
    eligible = [
        t
        for t in thoughts
        if t.is_open
        and t.kind == "idea"
        and _older_than(t.created_at, now, STALE_IDEA_DAYS)
        and (t.surfaced_at is None or _older_than(t.surfaced_at, now, STALE_IDEA_DAYS))
    ]
    eligible.sort(key=lambda t: t.created_at, reverse=True)
    return [
        Candidate(
            type="stale_idea",
            thought_ids=(t.thought_id,),
            content=t.content,
            context=None,
        )
        for t in eligible
    ]


def is_loop(
    matches: Sequence[DepotMatch],
    *,
    new_thought_id: int,
    member_created_ats: Mapping[int, datetime],
    now: datetime,
) -> int | None:
    """Taille de la boucle (nouveau dépôt inclus) ou None si pas de boucle.

    Réutilisé par le step 04 (suffixe d'accusé). Exclut l'auto-match du
    nouveau dépôt ; un match absent de `member_created_ats` est un orphelin
    ChromaDB → ignoré. Fenêtre `LOOP_WINDOW_DAYS` sur `created_at`.
    """
    neighbours = {
        m.thought_id
        for m in matches
        if m.thought_id != new_thought_id
        and m.thought_id in member_created_ats
        and _within(member_created_ats[m.thought_id], now, LOOP_WINDOW_DAYS)
    }
    size = 1 + len(neighbours)  # le nouveau dépôt compte dans la boucle
    return size if size >= LOOP_MIN_MEMBERS else None


def _within(reference: datetime, now: datetime, days: int) -> bool:
    """True si `reference` est dans les `days` derniers jours (inclus)."""
    return now - reference <= timedelta(days=days)


def _older_than(reference: datetime, now: datetime, days: int) -> bool:
    """True si `reference` date de strictement plus de `days` jours."""
    return now - reference > timedelta(days=days)


def _in_cooldown(surfaced_at: datetime | None, now: datetime) -> bool:
    """True si une restitution a eu lieu il y a moins de SURFACED_COOLDOWN_DAYS."""
    return surfaced_at is not None and _within(surfaced_at, now, SURFACED_COOLDOWN_DAYS)
