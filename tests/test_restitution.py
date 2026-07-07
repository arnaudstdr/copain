"""Tests purs des heuristiques de restitution (pas de SQLite, pas d'I/O)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import bot.thoughts.restitution
from bot.memory.manager import DepotMatch
from bot.thoughts.restitution import (
    Candidate,
    ConnectionFacts,
    LoopFacts,
    ThoughtFacts,
    is_loop,
    select_candidates,
)

NOW = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)


def _thought(
    thought_id: int,
    *,
    kind: str | None = "worry",
    days_ago: float = 0,
    surfaced_days_ago: float | None = None,
    is_open: bool = True,
    content: str = "contenu",
) -> ThoughtFacts:
    return ThoughtFacts(
        thought_id=thought_id,
        kind=kind,
        created_at=NOW - timedelta(days=days_ago),
        surfaced_at=(
            NOW - timedelta(days=surfaced_days_ago) if surfaced_days_ago is not None else None
        ),
        is_open=is_open,
        content=content,
    )


def _select(
    *,
    thoughts: list[ThoughtFacts] | None = None,
    loops: list[LoopFacts] | None = None,
    event_matched_worries: dict[int, str] | None = None,
    connections: list[ConnectionFacts] | None = None,
) -> list[Candidate]:
    return select_candidates(
        thoughts=thoughts or [],
        loops=loops or [],
        event_matched_worries=event_matched_worries or {},
        now=NOW,
        connections=connections or [],
    )


# ---------------------------------------------------------------------------
# closable_worry
# ---------------------------------------------------------------------------


def test_closable_worry_via_event_match() -> None:
    """Souci ouvert récent rapproché d'un évent passé → candidat avec contexte."""
    worry = _thought(1, days_ago=3, content="peur pour le contrôle technique")
    out = _select(thoughts=[worry], event_matched_worries={1: "Contrôle technique"})
    assert len(out) == 1
    assert out[0].type == "closable_worry"
    assert out[0].thought_ids == (1,)
    assert out[0].content == "peur pour le contrôle technique"
    assert out[0].context == "Contrôle technique"


def test_closable_worry_via_age() -> None:
    """Souci ouvert depuis > 14 j, sans évent rapproché → candidat sans contexte."""
    out = _select(thoughts=[_thought(1, days_ago=15)])
    assert len(out) == 1
    assert out[0].type == "closable_worry"
    assert out[0].context is None


def test_worry_recent_without_event_is_not_candidate() -> None:
    """Souci de 13 j sans évent rapproché → rien (seuil strict > 14 j)."""
    assert _select(thoughts=[_thought(1, days_ago=13)]) == []


def test_closed_worry_is_excluded() -> None:
    assert _select(thoughts=[_thought(1, days_ago=20, is_open=False)]) == []


def test_kind_null_excluded_from_closable_worry() -> None:
    """Dépôt non catégorisé : exclu de closable_worry même ancien + rapproché."""
    t = _thought(1, kind=None, days_ago=20)
    assert _select(thoughts=[t], event_matched_worries={1: "Évent"}) == []


def test_closable_worry_cooldown_excludes_recently_surfaced() -> None:
    """Restitué il y a < 7 j → exclu (anti-harcèlement)."""
    assert _select(thoughts=[_thought(1, days_ago=20, surfaced_days_ago=3)]) == []


def test_closable_worry_readmitted_after_cooldown() -> None:
    """Restitué il y a > 7 j → de nouveau candidat."""
    out = _select(thoughts=[_thought(1, days_ago=20, surfaced_days_ago=10)])
    assert len(out) == 1
    assert out[0].type == "closable_worry"


# ---------------------------------------------------------------------------
# loop
# ---------------------------------------------------------------------------


def _loop(*members: ThoughtFacts) -> LoopFacts:
    return LoopFacts(members=members)


def test_loop_detected_with_open_member() -> None:
    """≥ 3 membres sur 30 j dont ≥ 1 ouvert → candidat loop."""
    loop = _loop(
        _thought(1, days_ago=20, is_open=False),
        _thought(2, days_ago=10, is_open=False),
        _thought(3, days_ago=1, content="encore peur pour le boulot"),
    )
    out = _select(loops=[loop])
    assert len(out) == 1
    assert out[0].type == "loop"
    assert set(out[0].thought_ids) == {1, 2, 3}
    assert out[0].content == "encore peur pour le boulot"  # membre le plus récent
    assert out[0].context == "3 dépôts en 30 jours"


def test_loop_all_members_closed_is_not_candidate() -> None:
    loop = _loop(
        _thought(1, days_ago=20, is_open=False),
        _thought(2, days_ago=10, is_open=False),
        _thought(3, days_ago=1, is_open=False),
    )
    assert _select(loops=[loop]) == []


def test_loop_member_outside_window_not_counted() -> None:
    """Membre créé il y a > 30 j hors comptage → boucle sous le seuil."""
    loop = _loop(
        _thought(1, days_ago=40),
        _thought(2, days_ago=10),
        _thought(3, days_ago=1),
    )
    assert _select(loops=[loop]) == []


def test_loop_kind_null_members_count() -> None:
    """Les dépôts non catégorisés comptent dans les boucles."""
    loop = _loop(
        _thought(1, kind=None, days_ago=20),
        _thought(2, kind=None, days_ago=10),
        _thought(3, kind="worry", days_ago=1),
    )
    out = _select(loops=[loop])
    assert len(out) == 1
    assert out[0].type == "loop"


def test_loop_cooldown_excludes_recently_surfaced() -> None:
    """Boucle restituée il y a < 7 j, sans nouveau membre depuis → exclue."""
    loop = _loop(
        _thought(1, days_ago=20),
        _thought(2, days_ago=10, surfaced_days_ago=2),
        _thought(3, days_ago=8),
    )
    assert _select(loops=[loop]) == []


def test_loop_readmitted_when_new_member_since_surfaced() -> None:
    """Un membre créé après la dernière restitution → boucle réadmise."""
    loop = _loop(
        _thought(1, days_ago=20),
        _thought(2, days_ago=10, surfaced_days_ago=2),
        _thought(3, days_ago=1),  # créé après le surfaced_at d'il y a 2 j
    )
    out = _select(loops=[loop])
    assert len(out) == 1
    assert out[0].type == "loop"


# ---------------------------------------------------------------------------
# stale_idea
# ---------------------------------------------------------------------------


def test_stale_idea_never_surfaced() -> None:
    out = _select(thoughts=[_thought(1, kind="idea", days_ago=20, content="idée appli")])
    assert len(out) == 1
    assert out[0].type == "stale_idea"
    assert out[0].thought_ids == (1,)
    assert out[0].content == "idée appli"


def test_recent_idea_is_not_stale() -> None:
    assert _select(thoughts=[_thought(1, kind="idea", days_ago=10)]) == []


def test_stale_idea_surfaced_recently_is_excluded() -> None:
    """Restituée il y a < 14 j → pas encore re-restituable."""
    assert _select(thoughts=[_thought(1, kind="idea", days_ago=30, surfaced_days_ago=5)]) == []


def test_stale_idea_surfaced_long_ago_is_readmitted() -> None:
    out = _select(thoughts=[_thought(1, kind="idea", days_ago=40, surfaced_days_ago=20)])
    assert len(out) == 1
    assert out[0].type == "stale_idea"


def test_closed_idea_is_excluded() -> None:
    assert _select(thoughts=[_thought(1, kind="idea", days_ago=20, is_open=False)]) == []


def test_kind_null_excluded_from_stale_idea() -> None:
    assert _select(thoughts=[_thought(1, kind=None, days_ago=20)]) == []


# ---------------------------------------------------------------------------
# Priorité, plafond, ordre stable
# ---------------------------------------------------------------------------


def test_priority_and_cap_two_items() -> None:
    """closable_worry > loop > stale_idea, max 2 items."""
    worry = _thought(1, days_ago=20)
    idea = _thought(2, kind="idea", days_ago=20)
    loop = _loop(
        _thought(3, days_ago=10),
        _thought(4, days_ago=5),
        _thought(5, days_ago=1),
    )
    out = _select(thoughts=[worry, idea], loops=[loop])
    assert [c.type for c in out] == ["closable_worry", "loop"]


def test_stale_idea_fills_remaining_slot() -> None:
    worry = _thought(1, days_ago=20)
    idea = _thought(2, kind="idea", days_ago=20)
    out = _select(thoughts=[worry, idea])
    assert [c.type for c in out] == ["closable_worry", "stale_idea"]


def test_same_priority_most_recent_first() -> None:
    """À priorité égale, ordre stable : created_at le plus récent d'abord."""
    older = _thought(1, days_ago=25, content="vieux souci")
    newer = _thought(2, days_ago=16, content="souci plus récent")
    out = _select(thoughts=[older, newer])
    assert [c.thought_ids for c in out] == [(2,), (1,)]


def test_no_candidates_returns_empty() -> None:
    assert _select() == []


def test_thought_id_deduplicated_across_types() -> None:
    """Un thought_id déjà retenu exclut le candidat moins prioritaire qui le contient."""
    worry = _thought(1, days_ago=20)  # closable_worry (id 1)
    loop = _loop(  # boucle contenant le même id 1
        _thought(1, days_ago=20),
        _thought(2, days_ago=10),
        _thought(3, days_ago=1),
    )
    idea = _thought(4, kind="idea", days_ago=20)
    out = _select(thoughts=[worry, idea], loops=[loop])
    # La loop saute (id 1 déjà restitué via closable_worry), stale_idea prend le slot.
    assert [c.type for c in out] == ["closable_worry", "stale_idea"]
    assert out[0].thought_ids == (1,)
    assert out[1].thought_ids == (4,)


# ---------------------------------------------------------------------------
# connection (versant fertile du signal de proximité)
# ---------------------------------------------------------------------------


def _conn(a: ThoughtFacts, b: ThoughtFacts, distance: float = 0.2) -> ConnectionFacts:
    return ConnectionFacts(a=a, b=b, distance=distance)


def test_connection_surfaces_pair_content() -> None:
    """Une connexion seule ressort avec la graine en content et le voisin en context."""
    a = _thought(1, kind="idea", days_ago=1, content="idée A")
    b = _thought(2, kind="note", days_ago=8, content="note B")
    out = _select(connections=[_conn(a, b, 0.1)])
    assert len(out) == 1
    assert out[0].type == "connection"
    assert out[0].thought_ids == (1, 2)
    assert out[0].content == "idée A"
    assert out[0].context == "note B"


def test_connection_sorted_by_distance() -> None:
    """Le lien le plus fort (distance la plus faible) passe en premier."""
    a1 = _thought(1, days_ago=1, content="A1")
    b1 = _thought(2, days_ago=1, content="B1")
    a2 = _thought(3, days_ago=1, content="A2")
    b2 = _thought(4, days_ago=1, content="B2")
    out = _select(connections=[_conn(a1, b1, 0.24), _conn(a2, b2, 0.05)])
    assert [c.thought_ids for c in out] == [(3, 4), (1, 2)]


def test_connection_cooldown_excludes_if_either_member_recent() -> None:
    """Si l'un des deux dépôts a été restitué il y a < 7 j, la connexion attend."""
    a = _thought(1, days_ago=1, content="A", surfaced_days_ago=2)
    b = _thought(2, days_ago=1, content="B")
    assert _select(connections=[_conn(a, b)]) == []


def test_connection_dropped_when_sharing_id_with_loop() -> None:
    """Collision boucle/connexion : la boucle (prioritaire) évince la connexion."""
    loop = _loop(
        _thought(1, days_ago=10),
        _thought(2, days_ago=5),
        _thought(3, days_ago=1),
    )
    # Connexion partageant l'id 3 avec la boucle → écartée par la dédup.
    conn = _conn(_thought(3, days_ago=1, content="A"), _thought(9, days_ago=2, content="B"))
    out = _select(loops=[loop], connections=[conn])
    assert [c.type for c in out] == ["loop"]


def test_connection_lower_priority_than_loop_higher_than_stale_idea() -> None:
    """Ordre : loop > connection > stale_idea (cap 2)."""
    loop = _loop(
        _thought(1, days_ago=10),
        _thought(2, days_ago=5),
        _thought(3, days_ago=1),
    )
    conn = _conn(_thought(4, days_ago=1, content="A"), _thought(5, days_ago=2, content="B"))
    idea = _thought(6, kind="idea", days_ago=20)
    out = _select(thoughts=[idea], loops=[loop], connections=[conn])
    assert [c.type for c in out] == ["loop", "connection"]


# ---------------------------------------------------------------------------
# is_loop (helper du step 04)
# ---------------------------------------------------------------------------


def _match(thought_id: int, distance: float = 0.2) -> DepotMatch:
    return DepotMatch(thought_id=thought_id, content="…", distance=distance)


def test_is_loop_new_plus_two_neighbours() -> None:
    """Nouveau dépôt + 2 voisins distincts dans la fenêtre → boucle de 3."""
    created = {
        1: NOW - timedelta(days=10),
        2: NOW - timedelta(days=5),
    }
    size = is_loop(
        [_match(1), _match(2)],
        new_thought_id=99,
        member_created_ats=created,
        now=NOW,
    )
    assert size == 3


def test_is_loop_excludes_self_match() -> None:
    """L'auto-match du nouveau dépôt ne compte pas comme voisin."""
    created = {1: NOW - timedelta(days=10), 99: NOW}
    size = is_loop(
        [_match(99), _match(1)],
        new_thought_id=99,
        member_created_ats=created,
        now=NOW,
    )
    assert size is None  # 1 seul voisin distinct


def test_is_loop_neighbour_outside_window_excluded() -> None:
    created = {
        1: NOW - timedelta(days=40),  # hors fenêtre 30 j
        2: NOW - timedelta(days=5),
    }
    size = is_loop(
        [_match(1), _match(2)],
        new_thought_id=99,
        member_created_ats=created,
        now=NOW,
    )
    assert size is None


def test_is_loop_orphan_match_ignored() -> None:
    """Match ChromaDB sans ligne SQLite (absent du mapping) → ignoré."""
    created = {2: NOW - timedelta(days=5)}
    size = is_loop(
        [_match(1), _match(2)],  # 1 est orphelin
        new_thought_id=99,
        member_created_ats=created,
        now=NOW,
    )
    assert size is None


def test_is_loop_duplicate_matches_counted_once() -> None:
    created = {1: NOW - timedelta(days=5)}
    size = is_loop(
        [_match(1), _match(1), _match(1)],
        new_thought_id=99,
        member_created_ats=created,
        now=NOW,
    )
    assert size is None


def test_is_loop_larger_loop_returns_size() -> None:
    created = {i: NOW - timedelta(days=i) for i in range(1, 5)}
    size = is_loop(
        [_match(i) for i in range(1, 5)],
        new_thought_id=99,
        member_created_ats=created,
        now=NOW,
    )
    assert size == 5


# ---------------------------------------------------------------------------
# Pureté du module
# ---------------------------------------------------------------------------


def test_module_has_no_io_imports() -> None:
    """Le module pur ne doit tirer aucune dépendance d'I/O à l'exécution."""
    source = Path(bot.thoughts.restitution.__file__).read_text(encoding="utf-8")
    runtime_source = source.split("if TYPE_CHECKING:")[0]
    for forbidden in ("sqlalchemy", "chromadb", "httpx", "bot.memory"):
        assert forbidden not in runtime_source
