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

Le rapprochement worry ↔ évent passé combine un match **lexical** (tokens
significatifs partagés, rapide et sans I/O) et un **booster sémantique**
(embeddings via `MemoryManager.embed_texts`) fusionnés en union : le
sémantique attrape les synonymes que le lexical rate, et si l'Embedder est
indisponible on retombe proprement sur le seul lexical (fail-soft). Le petit
nombre d'évents sur 7 j borne le coût d'embed sur le Pi. L'ancienneté (> 14 j)
reste un déclencheur de `closable_worry` à part entière. Enfin, un souci
d'**argent** peut être apaisé par un budget sain (croisement avec
`compute_budget`, fail-soft, jamais quand le budget est tendu).
"""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bot.calendar.client import ICloudCalendarError
from bot.finance.config import extract_finance_config
from bot.finance.summary import load_budget_summary
from bot.logging_conf import get_logger
from bot.thoughts.restitution import (
    CLOSABLE_EVENT_WINDOW_DAYS,
    LOOP_MIN_MEMBERS,
    LOOP_WINDOW_DAYS,
    Candidate,
    ConnectionFacts,
    LoopFacts,
    ThoughtFacts,
    select_candidates,
)

if TYPE_CHECKING:
    from bot.calendar.client import ICloudCalendarClient
    from bot.calendar.models import CalendarEvent
    from bot.config import Settings
    from bot.finance.budget import BudgetSummary
    from bot.finance.config import FinanceConfig
    from bot.finance.manager import ExpenseManager
    from bot.llm.client import LLMClient
    from bot.memory.manager import DepotMatch, MemoryManager
    from bot.profile import UserProfile
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

# Vocabulaire de base pour repérer un souci « d'argent » (tokens déjà
# normalisés : sans accents, minuscule, longueur ≥ 4 pour passer le filtre de
# `_significant_tokens`). Complété à la volée par les libellés/catégories des
# enveloppes et récurrentes du profil (personnalisation gratuite).
_MONEY_TOKENS: frozenset[str] = frozenset(
    {
        "argent",
        "budget",
        "fric",
        "thune",
        "loyer",
        "facture",
        "factures",
        "banque",
        "compte",
        "comptes",
        "credit",
        "credits",
        "pret",
        "prets",
        "dette",
        "dettes",
        "decouvert",
        "depense",
        "depenses",
        "economies",
        "epargne",
        "salaire",
        "paye",
        "paie",
        "impot",
        "impots",
        "finances",
        "financier",
    }
)


@dataclass(frozen=True, slots=True)
class ForYouItem:
    """Item de restitution prêt pour la PWA : type, message formulé, ids ciblés."""

    type: str  # CandidateType : closable_worry | loop | connection | stale_idea
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
        expenses: ExpenseManager,
        profile: UserProfile,
        settings: Settings,
        *,
        similarity_max_distance: float,
        max_seeds: int = 20,
        top_k: int = 8,
    ) -> None:
        self._thoughts = thoughts
        self._memory = memory
        self._calendar = calendar
        self._llm = llm
        self._expenses = expenses
        self._profile = profile
        self._settings = settings
        self._max_distance = similarity_max_distance
        self._event_max_distance = settings.foryou_event_max_distance
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
        # Union lexical (rapide, fail-safe) et sémantique (attrape les
        # synonymes) : le lexical prime sur conflit. Embedder KO → sémantique
        # vide → comportement lexical d'origine.
        lexical = match_worries_to_events(worries, events)
        semantic = await self._safe_semantic_worry_events(worries, events)
        event_matched = {**semantic, **lexical}

        # Souci d'argent apaisé par un budget sain (fail-soft ; {} si budget
        # tendu ou non configuré — on ne rassure jamais à tort).
        budget_reassured = await self._safe_budget_reassurance(worries, now)

        # Une seule passe de similarité (un embed par graine ouverte) alimente
        # à la fois les boucles (≥ 3, versant anxieux) et les connexions
        # (paires, versant fertile). ChromaDB down → ni l'un ni l'autre.
        by_seed = await self._gather_similar(open_facts)
        loops = self._detect_loops(open_facts, facts_by_id, by_seed)
        connections = self._detect_connections(open_facts, facts_by_id, by_seed)

        candidates = select_candidates(
            thoughts=open_facts,
            loops=loops,
            event_matched_worries=event_matched,
            now=now,
            connections=connections,
            budget_reassured_worries=budget_reassured,
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

    async def _safe_semantic_worry_events(
        self,
        worries: Sequence[ThoughtFacts],
        events: Sequence[CalendarEvent],
    ) -> dict[int, str]:
        """Rapproche chaque souci de son évent passé le plus proche (sémantique).

        Un seul appel d'embeddings batché sur `[soucis…, titres d'évents…]`,
        puis meilleure paire cosine sous `foryou_event_max_distance`. Booster
        de `match_worries_to_events` : tout échec (Embedder KO) → `{}`, le
        lexical reste seul. Court-circuite sans embed si rien à apparier.
        """
        if not worries or not events:
            return {}
        try:
            worry_contents = [w.content for w in worries]
            titles = [e.title for e in events]
            vectors = await self._memory.embed_texts([*worry_contents, *titles])
            if len(vectors) != len(worry_contents) + len(titles):
                return {}
            worry_vecs = vectors[: len(worry_contents)]
            event_vecs = vectors[len(worry_contents) :]
            matched: dict[int, str] = {}
            for worry, wvec in zip(worries, worry_vecs, strict=True):
                best_title: str | None = None
                best_distance = self._event_max_distance
                for event, evec in zip(events, event_vecs, strict=True):
                    distance = 1.0 - _cosine(wvec, evec)
                    if distance <= best_distance:
                        best_distance = distance
                        best_title = event.title
                if best_title is not None:
                    matched[worry.thought_id] = best_title
            return matched
        except Exception as exc:  # Embedder/Ollama down → on garde le lexical
            log.warning("foryou_semantic_events_failed", error=str(exc))
            return {}

    async def _safe_budget_reassurance(
        self, worries: Sequence[ThoughtFacts], now: datetime
    ) -> dict[int, str]:
        """Soucis d'argent apaisables par un budget sain → {thought_id: phrase}.

        Gate strict : on ne renvoie une phrase que si le restant prévisionnel
        est positif ET aucune récurrente en retard ET aucune enveloppe
        dépassée. Sinon `{}` (jamais rassurer à tort ajouterait de l'anxiété).
        Entièrement fail-soft.
        """
        if not worries:
            return {}
        try:
            config = extract_finance_config(self._profile.data)
            summary = await load_budget_summary(
                expenses=self._expenses,
                config=config,
                timezone=self._settings.timezone,
            )
            if summary is None:
                return {}
            healthy = (
                summary.remaining_cents > 0
                and not summary.has_overdue
                and not summary.has_envelope_overrun
            )
            if not healthy:
                return {}
            vocabulary = _finance_vocabulary(config)
            money_ids = match_money_worries(worries, vocabulary)
            if not money_ids:
                return {}
            phrase = _budget_reassurance_phrase(summary)
            return dict.fromkeys(money_ids, phrase)
        except Exception as exc:  # YAML/SQLite/finance KO → pas d'angle budget
            log.warning("foryou_budget_reassurance_failed", error=str(exc))
            return {}

    async def _gather_similar(
        self, open_facts: Sequence[ThoughtFacts]
    ) -> dict[int, list[DepotMatch]] | None:
        """Voisins sémantiques par graine ouverte (un `find_similar_depots` chacun).

        Retourne `{thought_id: matches}` (matches triés par distance croissante,
        déjà filtrés par `max_distance`). `None` si ChromaDB est indisponible →
        ni boucle ni connexion (les autres candidats restent calculés).
        """
        by_seed: dict[int, list[DepotMatch]] = {}
        for seed in open_facts:
            try:
                by_seed[seed.thought_id] = await self._memory.find_similar_depots(
                    seed.content, top_k=self._top_k, max_distance=self._max_distance
                )
            except Exception as exc:  # ChromaDB down → ni boucle ni connexion
                log.warning("foryou_similar_lookup_failed", error=str(exc))
                return None
        return by_seed

    def _detect_loops(
        self,
        open_facts: Sequence[ThoughtFacts],
        facts_by_id: Mapping[int, ThoughtFacts],
        by_seed: dict[int, list[DepotMatch]] | None,
    ) -> list[LoopFacts]:
        """Regroupe les dépôts similaires en boucles, dédupliquées par chevauchement.

        Pur (consomme `by_seed` précalculé par `_gather_similar`). Les notes
        sont exclues (graine comme membre). `by_seed=None` (ChromaDB down) →
        aucune boucle. Deux graines qui partagent un membre désignent la même
        rumination → on n'en garde qu'une.
        """
        if by_seed is None:
            return []
        seeds = sorted(
            (f for f in open_facts if f.kind != "note"),
            key=lambda f: f.created_at,
            reverse=True,
        )
        loops: list[LoopFacts] = []
        kept_ids: set[int] = set()
        for seed in seeds:
            member_ids = {seed.thought_id}
            for match in by_seed.get(seed.thought_id, []):
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

    def _detect_connections(
        self,
        open_facts: Sequence[ThoughtFacts],
        facts_by_id: Mapping[int, ThoughtFacts],
        by_seed: dict[int, list[DepotMatch]] | None,
    ) -> list[ConnectionFacts]:
        """Relie chaque graine ouverte à son voisin le plus proche (tous kinds).

        Pur (consomme `by_seed`). Le voisin est le premier match (donc le plus
        proche) présent dans la fenêtre `facts_by_id` et distinct de la graine.
        Les paires sont dédupliquées de façon symétrique ({a,b} == {b,a}). La
        collision avec les boucles est gérée en aval par `select_candidates`
        (priorité boucle > connexion). `by_seed=None` → aucune connexion.
        """
        if by_seed is None:
            return []
        connections: list[ConnectionFacts] = []
        seen_pairs: set[frozenset[int]] = set()
        seeds = sorted(open_facts, key=lambda f: f.created_at, reverse=True)
        for seed in seeds:
            for match in by_seed.get(seed.thought_id, []):
                if match.thought_id == seed.thought_id:
                    continue
                other = facts_by_id.get(match.thought_id)
                if other is None:
                    continue
                pair = frozenset((seed.thought_id, other.thought_id))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    connections.append(ConnectionFacts(a=seed, b=other, distance=match.distance))
                break  # un seul voisin (le plus proche) par graine
        return connections

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
    worries: Iterable[ThoughtFacts],
    events: Iterable[CalendarEvent],
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
                matched[worry.thought_id] = title
                break
    return matched


def _significant_tokens(text: str) -> set[str]:
    """Tokens normalisés (sans accents, minuscule), longs et non vides de sens."""
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(c for c in normalized if not unicodedata.combining(c))
    raw = "".join(c if c.isalnum() else " " for c in ascii_text).split()
    return {tok for tok in raw if len(tok) >= _MIN_TOKEN_LEN and tok not in _STOPWORDS}


def match_money_worries(
    worries: Iterable[ThoughtFacts],
    vocabulary: frozenset[str],
) -> set[int]:
    """`thought_id` des soucis dont le contenu croise le vocabulaire finance.

    Même normalisation lexicale que `match_worries_to_events` (réutilise
    `_significant_tokens`). Pur, sans I/O.
    """
    return {
        worry.thought_id for worry in worries if _significant_tokens(worry.content) & vocabulary
    }


def _finance_vocabulary(config: FinanceConfig) -> frozenset[str]:
    """Vocabulaire finance = base FR + libellés/catégories du profil.

    Les libellés d'enveloppes et de récurrentes (loyer, essence, courses…)
    passent par `_significant_tokens` pour rester homogènes avec le contenu
    des soucis (accents/casse normalisés, tokens ≥ 4).
    """
    vocab: set[str] = set(_MONEY_TOKENS)
    for env in config.envelopes:
        vocab |= _significant_tokens(env.label)
        vocab |= _significant_tokens(env.category)
    for rec in config.recurring:
        vocab |= _significant_tokens(rec.label)
        if rec.category:
            vocab |= _significant_tokens(rec.category)
    return frozenset(vocab)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Similarité cosine de deux vecteurs (nomic-embed-text non normalisés)."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _budget_reassurance_phrase(summary: BudgetSummary) -> str:
    """Matière factuelle (pas la phrase finale) pour rassurer sur l'argent."""
    remaining = round(summary.remaining_cents / 100)
    return (
        f"le budget du mois est sous contrôle : il reste environ {remaining} € "
        "de restant prévisionnel, aucune récurrente en retard"
    )


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
    if candidate.type == "connection":
        other = (candidate.context or "").strip()
        return f"Ça en rejoint une autre : « {content} » fait écho à « {other} »."
    if candidate.context_kind == "budget" and candidate.context:
        return f"Tu t'inquiétais pour « {content} » — pour info, {candidate.context}."
    if candidate.context:  # évènement passé rapproché
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
            "contexte_type": c.context_kind,
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
    '- closable_worry : selon `contexte_type` — "event" : le `contexte` est un '
    "évènement passé, suggère doucement que le souci est peut-être réglé depuis "
    '(propose, n\'affirme jamais) ; "budget" : le `contexte` décrit un budget '
    "sain, rassure sobrement en reprenant l'info chiffrée, sans injonction ni "
    "dramatisation ; sinon (contexte vide) : demande doucement si c'est toujours "
    "d'actualité.\n"
    "- loop : objective sans juger qu'un sujet revient souvent ces temps-ci.\n"
    "- stale_idea : fais resurgir l'idée sans pression.\n"
    "- connection : signale sobrement que deux dépôts se font écho, sur un ton "
    "fertile (pas anxieux) — ici `contenu` et `contexte` portent les DEUX "
    "dépôts reliés, cite-les tous les deux entre « … ».\n\n"
    "RÈGLE ABSOLUE : chaque phrase doit NOMMER le sujet concerné en citant le "
    "champ `contenu` entre guillemets « … » (tu peux le raccourcir s'il est "
    "long, sans en changer le sens). Arnaud lit la phrase seule, sans aucun "
    "autre contexte : s'il ne peut pas savoir DE QUOI tu parles, la phrase est "
    "ratée. N'écris jamais « ce sujet », « cette pensée » ou « cette idée » "
    "sans préciser laquelle.\n"
    "Exemples : pour un loop sur « la santé de mon père », écris « Ça revient "
    "souvent en ce moment : « la santé de ton père ». » ; pour un "
    "closable_worry sur « le contrôle technique », écris « Tu avais en tête "
    "« le contrôle technique » — c'est peut-être réglé depuis ? ».\n\n"
    "Réponds UNIQUEMENT par un tableau JSON de chaînes, une par item, dans "
    "l'ordre reçu (champ `index`). Pas de markdown, pas de bloc <meta>, pas "
    'de texte autour. Exemple : ["…", "…"]'
)
