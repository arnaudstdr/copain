"""Tests de l'orchestrateur de restitution `ForYouBuilder` (card "Pour toi").

Tout est mocké (SQLite via `ThoughtManager`, ChromaDB via `MemoryManager`,
calendrier iCloud, LLM) : on vérifie le câblage heuristiques ↔ I/O, le
plafond à deux items, l'écriture du cooldown (`mark_surfaced`) et les modes
dégradés (LLM/calendrier/ChromaDB down) qui ne doivent jamais lever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.calendar.client import ICloudCalendarError
from bot.llm.client import LLMError
from bot.memory.manager import DepotMatch
from bot.thoughts import foryou as foryou_module
from bot.thoughts.foryou import (
    ForYouBuilder,
    ForYouItem,
    ForYouResult,
    match_money_worries,
    match_worries_to_events,
)
from bot.thoughts.restitution import ThoughtFacts

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _thought(
    thought_id: int,
    *,
    kind: str | None,
    age_days: float,
    is_open: bool = True,
    surfaced_days: float | None = None,
    content: str | None = None,
) -> SimpleNamespace:
    """Fabrique un faux `Thought` (datetimes naïves, comme SQLite les rend)."""
    created = (NOW - timedelta(days=age_days)).replace(tzinfo=None)
    surfaced = (
        None
        if surfaced_days is None
        else (NOW - timedelta(days=surfaced_days)).replace(tzinfo=None)
    )
    return SimpleNamespace(
        id=thought_id,
        content=content or f"pensée {thought_id}",
        kind=kind,
        created_at=created,
        processed_at=None if is_open else created,
        surfaced_at=surfaced,
    )


def _build(
    *,
    open_thoughts: list[SimpleNamespace],
    window_thoughts: list[SimpleNamespace] | None = None,
    matches: list[DepotMatch] | BaseException = (),  # type: ignore[assignment]
    events: list[SimpleNamespace] | BaseException = (),  # type: ignore[assignment]
    calendar_connected: bool = True,
    llm_response: str | BaseException = '["formulé"]',
    embeddings: list[list[float]] | BaseException | None = None,
    profile_data: dict[str, object] | None = None,
) -> tuple[ForYouBuilder, MagicMock, MagicMock, MagicMock, MagicMock]:
    """Assemble un ForYouBuilder avec ses collaborateurs mockés.

    Par défaut le budget n'est pas configuré (`profile_data={}`) → aucun angle
    budget, et `embed_texts` renvoie `[]` → booster sémantique neutre (le
    lexical reste seul). Les tests budget patchent `load_budget_summary`.
    """
    thoughts = MagicMock()
    thoughts.list_open = AsyncMock(return_value=open_thoughts)
    thoughts.list_since = AsyncMock(
        return_value=open_thoughts if window_thoughts is None else window_thoughts
    )
    thoughts.mark_surfaced = AsyncMock()

    memory = MagicMock()
    if isinstance(matches, BaseException):
        memory.find_similar_depots = AsyncMock(side_effect=matches)
    else:
        memory.find_similar_depots = AsyncMock(return_value=list(matches))
    if isinstance(embeddings, BaseException):
        memory.embed_texts = AsyncMock(side_effect=embeddings)
    else:
        memory.embed_texts = AsyncMock(return_value=[] if embeddings is None else embeddings)

    calendar = MagicMock()
    calendar.is_connected = calendar_connected
    if isinstance(events, BaseException):
        calendar.list_all_between = AsyncMock(side_effect=events)
    else:
        calendar.list_all_between = AsyncMock(return_value=list(events))

    llm = MagicMock()
    if isinstance(llm_response, BaseException):
        llm.chat = AsyncMock(side_effect=llm_response)
    else:
        llm.chat = AsyncMock(return_value=llm_response)

    expenses = MagicMock()
    profile = SimpleNamespace(data=profile_data if profile_data is not None else {})
    settings = SimpleNamespace(timezone="Europe/Paris", foryou_event_max_distance=0.4)

    builder = ForYouBuilder(
        thoughts=thoughts,
        memory=memory,
        calendar=calendar,
        llm=llm,
        expenses=expenses,
        profile=profile,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
        similarity_max_distance=0.35,
    )
    return builder, thoughts, memory, calendar, llm


# --- helper lexical worry ↔ évent ------------------------------------------


def _facts(thought_id: int, content: str) -> ThoughtFacts:
    """Fabrique un `ThoughtFacts` (la forme réellement reçue par le helper).

    Contrairement à `_thought` qui mime une ligne SQLite (champ `id`),
    `match_worries_to_events` consomme des `ThoughtFacts` (champ `thought_id`)
    — utiliser ici la vraie forme aurait attrapé la régression `worry.id`.
    """
    return ThoughtFacts(
        thought_id=thought_id,
        kind="worry",
        created_at=NOW,
        surfaced_at=None,
        is_open=True,
        content=content,
    )


def test_match_worries_to_events_matches_on_shared_token() -> None:
    worries = [_facts(1, "angoisse pour le dentiste")]
    events = [SimpleNamespace(title="Rendez-vous dentiste")]
    matched = match_worries_to_events(worries, events)
    assert matched == {1: "Rendez-vous dentiste"}


def test_match_worries_to_events_ignores_stopwords_and_short_tokens() -> None:
    worries = [_facts(1, "peur pour le truc")]
    events = [SimpleNamespace(title="pour avec dans")]
    assert match_worries_to_events(worries, events) == {}


# --- build() : plafond + cooldown ------------------------------------------


async def test_build_caps_at_two_items_and_marks_all_surfaced() -> None:
    # closable_worry (âge) + loop (3 membres) + stale_idea → 3 éligibles, cap 2.
    open_thoughts = [
        _thought(1, kind="worry", age_days=20),  # closable par ancienneté
        _thought(2, kind="idea", age_days=20),  # stale_idea
        _thought(3, kind="worry", age_days=2),  # seed boucle
        _thought(4, kind="worry", age_days=5),
        _thought(5, kind="worry", age_days=8),
    ]
    loop_matches = [
        DepotMatch(thought_id=3, content="m3", distance=0.1),
        DepotMatch(thought_id=4, content="m4", distance=0.2),
        DepotMatch(thought_id=5, content="m5", distance=0.3),
    ]
    builder, thoughts, _memory, _cal, llm = _build(
        open_thoughts=open_thoughts,
        matches=loop_matches,
        llm_response='["msg A", "msg B"]',
    )

    result = await builder.build(now=NOW)

    assert isinstance(result, ForYouResult)
    assert [it.type for it in result.items] == ["closable_worry", "loop"]
    assert result.items[0].message == "msg A"
    assert result.items[1].message == "msg B"
    assert result.items[1].thought_ids == (3, 4, 5)
    llm.chat.assert_awaited_once()
    # cooldown écrit pour tous les ids restitués (1 = worry, 3/4/5 = boucle).
    (surfaced_ids,), _ = thoughts.mark_surfaced.call_args
    assert sorted(surfaced_ids) == [1, 3, 4, 5]


# --- modes dégradés --------------------------------------------------------


async def test_llm_down_falls_back_to_templates_and_still_marks_surfaced() -> None:
    builder, thoughts, _m, _c, _llm = _build(
        open_thoughts=[_thought(1, kind="worry", age_days=20, content="le rapport annuel")],
        llm_response=LLMError("ollama down"),
    )

    result = await builder.build(now=NOW)

    assert len(result.items) == 1
    assert result.items[0].type == "closable_worry"
    assert "rapport annuel" in result.items[0].message  # template, pas vide
    thoughts.mark_surfaced.assert_awaited_once()
    (surfaced_ids,), _ = thoughts.mark_surfaced.call_args
    assert list(surfaced_ids) == [1]


async def test_llm_invalid_json_falls_back_to_templates() -> None:
    builder, _t, _m, _c, _llm = _build(
        open_thoughts=[_thought(1, kind="worry", age_days=20, content="le rapport annuel")],
        llm_response="désolé je ne sais pas formuler ça",
    )

    result = await builder.build(now=NOW)

    assert len(result.items) == 1
    assert "rapport annuel" in result.items[0].message


async def test_calendar_down_degrades_to_age_only() -> None:
    # W1 surfaçable par âge (>14j), W2 récent (5j) ne tiendrait que par un
    # rapprochement évent : calendrier down → seul W1 doit ressortir.
    open_thoughts = [
        _thought(1, kind="worry", age_days=20, content="vieux souci"),
        _thought(2, kind="worry", age_days=5, content="souci récent"),
    ]
    builder, _t, _m, _c, _llm = _build(
        open_thoughts=open_thoughts,
        events=ICloudCalendarError("caldav down"),
    )

    result = await builder.build(now=NOW)

    assert [it.type for it in result.items] == ["closable_worry"]
    assert result.items[0].thought_ids == (1,)


async def test_build_matches_recent_worry_to_past_event() -> None:
    # Régression : un souci récent (5j, non surfaçable par âge) doit ressortir
    # via le rapprochement lexical à un évent passé. Exerce le chemin que les
    # autres tests build() esquivent (events=()) — il levait AttributeError
    # (worry.id sur un ThoughtFacts) avant le correctif, avalé en items:[].
    open_thoughts = [_thought(1, kind="worry", age_days=5, content="angoisse pour le dentiste")]
    builder, _t, _m, _c, _llm = _build(
        open_thoughts=open_thoughts,
        events=[SimpleNamespace(title="Rendez-vous dentiste")],
    )

    result = await builder.build(now=NOW)

    assert [it.type for it in result.items] == ["closable_worry"]
    assert result.items[0].thought_ids == (1,)


async def test_chromadb_down_no_loop_but_worry_and_idea_present() -> None:
    open_thoughts = [
        _thought(1, kind="worry", age_days=20),
        _thought(2, kind="idea", age_days=20),
    ]
    builder, _t, _m, _c, _llm = _build(
        open_thoughts=open_thoughts,
        matches=RuntimeError("chroma down"),
    )

    result = await builder.build(now=NOW)

    types = {it.type for it in result.items}
    assert types == {"closable_worry", "stale_idea"}
    assert "loop" not in types


async def test_build_emits_connection_for_two_close_depots() -> None:
    """Deux dépôts proches (< 3 membres, pas de boucle) → item connexion."""
    open_thoughts = [
        _thought(1, kind="note", age_days=1, content="acheter un vélo cargo"),
        _thought(2, kind="note", age_days=8, content="idée de vélo pour les enfants"),
    ]
    # find_similar_depots renvoie la même liste pour chaque graine (mock) :
    # chaque dépôt trouve l'autre comme voisin le plus proche.
    matches = [
        DepotMatch(thought_id=1, content="acheter un vélo cargo", distance=0.05),
        DepotMatch(thought_id=2, content="idée de vélo pour les enfants", distance=0.1),
    ]
    builder, thoughts, _m, _c, _llm = _build(
        open_thoughts=open_thoughts,
        matches=matches,
        llm_response='["ça se rejoint"]',
    )

    result = await builder.build(now=NOW)

    assert [it.type for it in result.items] == ["connection"]
    assert set(result.items[0].thought_ids) == {1, 2}
    (surfaced_ids,), _ = thoughts.mark_surfaced.call_args
    assert sorted(surfaced_ids) == [1, 2]


async def test_build_no_connection_when_single_open_depot() -> None:
    """Un seul dépôt ouvert sans voisin → aucune connexion."""
    builder, _t, _m, _c, _llm = _build(
        open_thoughts=[_thought(1, kind="note", age_days=1, content="seul dépôt")],
        matches=[],
    )
    result = await builder.build(now=NOW)
    assert result.items == []


async def test_zero_candidates_returns_empty_items() -> None:
    builder, thoughts, _m, _c, llm = _build(open_thoughts=[])

    result = await builder.build(now=NOW)

    assert result.items == []
    llm.chat.assert_not_awaited()
    thoughts.mark_surfaced.assert_not_awaited()


async def test_fetched_at_is_the_provided_now() -> None:
    builder, *_ = _build(open_thoughts=[])
    result = await builder.build(now=NOW)
    assert result.fetched_at == NOW


def test_foryou_item_is_frozen() -> None:
    item = ForYouItem(type="loop", message="x", thought_ids=(1,))
    with pytest.raises(AttributeError):
        item.message = "y"  # type: ignore[misc]


# --- helper lexical money-worry --------------------------------------------


def test_match_money_worries_matches_base_vocabulary() -> None:
    worries = [_facts(1, "j'ai peur pour le loyer"), _facts(2, "penser à arroser les plantes")]
    matched = match_money_worries(worries, frozenset({"loyer", "argent"}))
    assert matched == {1}


# --- piste 1 : souci d'argent apaisé par un budget sain --------------------


def _healthy(**overrides: object) -> SimpleNamespace:
    """Faux BudgetSummary (seuls les champs lus par le gate strict)."""
    base = {"remaining_cents": 42000, "has_overdue": False, "has_envelope_overrun": False}
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_money_worry_surfaced_when_budget_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Souci d'argent récent (age 2 j) : ni ancien, ni évent → SEUL le budget
    # sain peut le rendre closable.
    open_thoughts = [_thought(1, kind="worry", age_days=2, content="peur pour l'argent")]
    builder, _t, _m, _c, llm = _build(open_thoughts=open_thoughts, llm_response='["formulé"]')
    monkeypatch.setattr(foryou_module, "load_budget_summary", AsyncMock(return_value=_healthy()))

    result = await builder.build(now=NOW)

    assert [it.type for it in result.items] == ["closable_worry"]
    assert result.items[0].thought_ids == (1,)
    # Le payload envoyé au LLM porte bien le contexte de type budget.
    user_payload = llm.chat.call_args.kwargs["messages"][1]["content"]
    assert '"contexte_type": "budget"' in user_payload
    assert "restant prévisionnel" in user_payload


async def test_money_worry_template_reassures_when_llm_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_thoughts = [_thought(1, kind="worry", age_days=2, content="peur pour l'argent")]
    builder, *_ = _build(open_thoughts=open_thoughts, llm_response=LLMError("down"))
    monkeypatch.setattr(foryou_module, "load_budget_summary", AsyncMock(return_value=_healthy()))

    result = await builder.build(now=NOW)

    assert result.items[0].message.startswith("Tu t'inquiétais pour")
    assert "restant prévisionnel" in result.items[0].message


async def test_money_worry_not_surfaced_when_budget_has_overdue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_thoughts = [_thought(1, kind="worry", age_days=2, content="peur pour l'argent")]
    builder, *_ = _build(open_thoughts=open_thoughts)
    monkeypatch.setattr(
        foryou_module,
        "load_budget_summary",
        AsyncMock(return_value=_healthy(has_overdue=True)),
    )

    result = await builder.build(now=NOW)

    assert result.items == []


async def test_money_worry_not_surfaced_when_remaining_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_thoughts = [_thought(1, kind="worry", age_days=2, content="peur pour l'argent")]
    builder, *_ = _build(open_thoughts=open_thoughts)
    monkeypatch.setattr(
        foryou_module,
        "load_budget_summary",
        AsyncMock(return_value=_healthy(remaining_cents=-500)),
    )

    result = await builder.build(now=NOW)

    assert result.items == []


async def test_non_money_worry_ignored_even_if_budget_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Souci récent SANS lexique argent → le budget sain ne le concerne pas.
    open_thoughts = [_thought(1, kind="worry", age_days=2, content="peur pour le chat")]
    builder, *_ = _build(open_thoughts=open_thoughts)
    monkeypatch.setattr(foryou_module, "load_budget_summary", AsyncMock(return_value=_healthy()))

    result = await builder.build(now=NOW)

    assert result.items == []


# --- piste 2 : booster sémantique worry ↔ évent ----------------------------


async def test_semantic_booster_matches_worry_without_shared_token() -> None:
    # Aucun token partagé ("dents" vs "praticien") → seul le sémantique matche.
    open_thoughts = [_thought(1, kind="worry", age_days=2, content="angoisse liée aux dents")]
    events = [SimpleNamespace(title="Contrôle chez le praticien")]
    # Ordre des vecteurs : [worry1, event1] ; quasi colinéaires → distance ~0.
    builder, *_ = _build(
        open_thoughts=open_thoughts,
        events=events,
        embeddings=[[1.0, 0.0], [0.99, 0.01]],
        llm_response='["formulé"]',
    )

    result = await builder.build(now=NOW)

    assert [it.type for it in result.items] == ["closable_worry"]
    assert result.items[0].thought_ids == (1,)


async def test_semantic_booster_ignores_distant_event() -> None:
    open_thoughts = [_thought(1, kind="worry", age_days=2, content="angoisse liée aux dents")]
    events = [SimpleNamespace(title="Réunion budget trimestriel")]
    # Vecteurs orthogonaux → distance ~1 > seuil → pas de rapprochement.
    builder, *_ = _build(
        open_thoughts=open_thoughts,
        events=events,
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
    )

    result = await builder.build(now=NOW)

    assert result.items == []


async def test_embedder_down_falls_back_to_lexical_match() -> None:
    # Embedder KO : le lexical (token "dentiste" partagé) doit encore matcher.
    open_thoughts = [_thought(1, kind="worry", age_days=2, content="angoisse pour le dentiste")]
    events = [SimpleNamespace(title="Rendez-vous dentiste")]
    builder, *_ = _build(
        open_thoughts=open_thoughts,
        events=events,
        embeddings=RuntimeError("embedder down"),
        llm_response='["formulé"]',
    )

    result = await builder.build(now=NOW)

    assert [it.type for it in result.items] == ["closable_worry"]
    assert result.items[0].thought_ids == (1,)
