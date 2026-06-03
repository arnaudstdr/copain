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
from bot.thoughts.foryou import ForYouBuilder, ForYouItem, ForYouResult, match_worries_to_events

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
) -> tuple[ForYouBuilder, MagicMock, MagicMock, MagicMock, MagicMock]:
    """Assemble un ForYouBuilder avec ses collaborateurs mockés."""
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

    builder = ForYouBuilder(
        thoughts=thoughts,
        memory=memory,
        calendar=calendar,
        llm=llm,
        similarity_max_distance=0.35,
    )
    return builder, thoughts, memory, calendar, llm


# --- helper lexical worry ↔ évent ------------------------------------------


def test_match_worries_to_events_matches_on_shared_token() -> None:
    worries = [_thought(1, kind="worry", age_days=3, content="angoisse pour le dentiste")]
    events = [SimpleNamespace(title="Rendez-vous dentiste")]
    matched = match_worries_to_events(worries, events)
    assert matched == {1: "Rendez-vous dentiste"}


def test_match_worries_to_events_ignores_stopwords_and_short_tokens() -> None:
    worries = [_thought(1, kind="worry", age_days=3, content="peur pour le truc")]
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
