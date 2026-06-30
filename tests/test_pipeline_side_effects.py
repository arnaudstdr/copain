"""Tests isolés des side effects du pipeline (`bot/pipeline/side_effects.py`).

On appelle `apply_side_effects` et ses sous-routines directement, avec une
`Meta` fabriquée et un `BotDeps` mocké, pour couvrir :
- le routing par intent (expense / depot close / depot add / memory / task) ;
- les chemins fail-soft (indexation ChromaDB qui échoue, détection de boucle
  qui lève, YAML finance illisible) ;
- la conversion euros→cents et la clôture en langage naturel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.memory.manager import DepotMatch
from bot.pipeline.side_effects import (
    CLOSE_NOT_FOUND_TEXT,
    _close_thought_from_meta,
    _detect_depot_loop,
    apply_side_effects,
    euros_to_cents,
    handle_expense_side_effect,
    record_depot,
    safe_open_worries,
    safe_pending_recurring,
)
from tests.conftest import make_meta

if TYPE_CHECKING:
    from bot.pipeline.core import BotDeps


def _thought(thought_id: int, *, created_offset_days: float = 0.0) -> MagicMock:
    """Thought factice avec un `created_at` naïf UTC (comme les lignes SQLite)."""
    t = MagicMock()
    t.id = thought_id
    t.created_at = (datetime.now(UTC) - timedelta(days=created_offset_days)).replace(tzinfo=None)
    t.content = f"pensée {thought_id}"
    t.kind = "worry"
    return t


# --- apply_side_effects : routing -------------------------------------------


async def test_answer_intent_no_side_effect(bot_deps: BotDeps) -> None:
    outcome = await apply_side_effects("salut", make_meta(intent="answer"), bot_deps)
    assert outcome.loop_size is None and outcome.replace_text is None
    bot_deps.memory.store.assert_not_called()
    bot_deps.tasks.create.assert_not_called()


async def test_memory_store_when_flagged(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="memory", store_memory=True, memory_content="Arnaud aime le thé.")
    await apply_side_effects("je bois du thé", meta, bot_deps)
    bot_deps.memory.store.assert_awaited_once()
    _, kwargs = bot_deps.memory.store.call_args
    assert kwargs["memory_content"] == "Arnaud aime le thé."


async def test_task_with_due_creates_and_schedules(bot_deps: BotDeps) -> None:
    meta = make_meta(
        intent="task", task={"content": "arroser les plantes", "due_str": "demain 18h"}
    )
    await apply_side_effects("rappelle-moi", meta, bot_deps)
    bot_deps.tasks.create.assert_awaited_once()
    bot_deps.scheduler.add_reminder.assert_called_once()


async def test_task_without_due_no_reminder(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="task", task={"content": "acheter du pain", "due_str": None})
    await apply_side_effects("note", meta, bot_deps)
    bot_deps.tasks.create.assert_awaited_once()
    bot_deps.scheduler.add_reminder.assert_not_called()


async def test_expense_intent_routes_to_expense_handler(bot_deps: BotDeps) -> None:
    meta = make_meta(
        intent="expense",
        expense={"action": "spend", "amount": 12.5, "label": "café"},
    )
    outcome = await apply_side_effects("j'ai dépensé 12,50", meta, bot_deps)
    assert outcome.loop_size is None
    bot_deps.expenses.add_punctual.assert_awaited_once()
    # Le store_memory générique ne doit PAS être déclenché sur le chemin expense.
    bot_deps.memory.store.assert_not_called()


# --- apply_side_effects : dépôt cognitif -------------------------------------


async def test_depot_add_persists_and_indexes(bot_deps: BotDeps) -> None:
    bot_deps.memory.find_similar_depots = AsyncMock(return_value=[])
    meta = make_meta(intent="depot", depot={"content": "j'ai peur pour X", "kind": "worry"})
    outcome = await apply_side_effects("j'ai peur", meta, bot_deps)
    bot_deps.thoughts.create.assert_awaited_once()
    bot_deps.memory.store_depot.assert_awaited_once()
    assert outcome.loop_size is None  # aucune boucle (pas de voisins)
    bot_deps.memory.store.assert_not_called()


async def test_depot_add_chroma_failure_is_soft(bot_deps: BotDeps) -> None:
    # L'indexation ChromaDB lève : le dépôt est créé quand même, loop_size None.
    bot_deps.memory.store_depot = AsyncMock(side_effect=RuntimeError("chroma down"))
    meta = make_meta(intent="depot", depot={"content": "tracas", "kind": "worry"})
    outcome = await apply_side_effects("tracas", meta, bot_deps)
    bot_deps.thoughts.create.assert_awaited_once()
    assert outcome.loop_size is None
    # find_similar_depots n'est jamais atteint si l'indexation échoue.
    bot_deps.memory.find_similar_depots.assert_not_called()


async def test_depot_add_detects_loop(bot_deps: BotDeps) -> None:
    new = MagicMock(id=1, content="encore ce souci", kind="worry")
    bot_deps.thoughts.create = AsyncMock(return_value=new)
    bot_deps.memory.store_depot = AsyncMock()
    bot_deps.memory.find_similar_depots = AsyncMock(
        return_value=[
            DepotMatch(thought_id=2, content="souci", distance=0.1),
            DepotMatch(thought_id=3, content="souci", distance=0.2),
        ]
    )
    bot_deps.thoughts.list_since = AsyncMock(
        return_value=[_thought(2, created_offset_days=1), _thought(3, created_offset_days=2)]
    )
    meta = make_meta(intent="depot", depot={"content": "encore", "kind": "worry"})
    outcome = await apply_side_effects("encore", meta, bot_deps)
    assert outcome.loop_size == 3  # nouveau + 2 voisins récents


async def test_record_depot_helper_shared_path(bot_deps: BotDeps) -> None:
    """`record_depot` (utilisé aussi par POST /thoughts) crée, indexe et retourne le tuple."""
    new = MagicMock(id=4, content="vidage", kind="note")
    bot_deps.thoughts.create = AsyncMock(return_value=new)
    bot_deps.memory.store_depot = AsyncMock()
    bot_deps.memory.find_similar_depots = AsyncMock(return_value=[])
    thought, loop_size = await record_depot(content="vidage", kind="note", deps=bot_deps)
    assert thought is new
    assert loop_size is None
    bot_deps.thoughts.create.assert_awaited_once_with(content="vidage", kind="note")
    bot_deps.memory.store_depot.assert_awaited_once()


# --- apply_side_effects : clôture en langage naturel -------------------------


async def test_depot_close_valid_id(bot_deps: BotDeps) -> None:
    bot_deps.thoughts.list_open = AsyncMock(return_value=[_thought(5)])
    bot_deps.thoughts.close = AsyncMock(return_value=True)
    meta = make_meta(intent="depot", depot={"action": "close", "thought_id": 5})
    outcome = await apply_side_effects("c'est réglé", meta, bot_deps)
    assert outcome.replace_text is None
    bot_deps.thoughts.close.assert_awaited_once_with(5)


async def test_depot_close_unknown_id(bot_deps: BotDeps) -> None:
    bot_deps.thoughts.list_open = AsyncMock(return_value=[_thought(5)])
    meta = make_meta(intent="depot", depot={"action": "close", "thought_id": 99})
    outcome = await apply_side_effects("c'est réglé", meta, bot_deps)
    assert outcome.replace_text == CLOSE_NOT_FOUND_TEXT
    bot_deps.thoughts.close.assert_not_called()


async def test_close_thought_none_id(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="depot", depot={"action": "close", "thought_id": None})
    outcome = await _close_thought_from_meta(meta, bot_deps)
    assert outcome.replace_text == CLOSE_NOT_FOUND_TEXT


# --- _detect_depot_loop ------------------------------------------------------


async def test_detect_loop_no_matches(bot_deps: BotDeps) -> None:
    bot_deps.memory.find_similar_depots = AsyncMock(return_value=[])
    assert await _detect_depot_loop(_thought(1), bot_deps) is None


async def test_detect_loop_below_threshold(bot_deps: BotDeps) -> None:
    bot_deps.memory.find_similar_depots = AsyncMock(
        return_value=[DepotMatch(thought_id=2, content="x", distance=0.1)]
    )
    bot_deps.thoughts.list_since = AsyncMock(return_value=[_thought(2)])
    # 1 voisin → taille 2 < LOOP_MIN_MEMBERS (3) → pas de boucle.
    assert await _detect_depot_loop(_thought(1), bot_deps) is None


async def test_detect_loop_exception_is_soft(bot_deps: BotDeps) -> None:
    bot_deps.memory.find_similar_depots = AsyncMock(side_effect=RuntimeError("boom"))
    assert await _detect_depot_loop(_thought(1), bot_deps) is None


# --- handle_expense_side_effect ----------------------------------------------


async def test_expense_action_none_noop(bot_deps: BotDeps) -> None:
    await handle_expense_side_effect(make_meta(intent="expense"), bot_deps)
    bot_deps.expenses.add_punctual.assert_not_called()


async def test_expense_spend_records(bot_deps: BotDeps) -> None:
    meta = make_meta(
        intent="expense",
        expense={"action": "spend", "amount": 12.5, "label": "café", "category": "resto"},
    )
    await handle_expense_side_effect(meta, bot_deps)
    bot_deps.expenses.add_punctual.assert_awaited_once()
    _, kwargs = bot_deps.expenses.add_punctual.call_args
    assert kwargs["amount_cents"] == 1250 and kwargs["label"] == "café"


async def test_expense_spend_invalid_amount_skipped(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="expense", expense={"action": "spend", "amount": None})
    await handle_expense_side_effect(meta, bot_deps)
    bot_deps.expenses.add_punctual.assert_not_called()


async def test_expense_income_starts_cycle_without_amount(bot_deps: BotDeps) -> None:
    meta = make_meta(
        intent="expense",
        expense={"action": "income", "amount": None, "starts_cycle": True},
    )
    await handle_expense_side_effect(meta, bot_deps)
    bot_deps.expenses.start_cycle.assert_awaited_once()
    bot_deps.expenses.add_income.assert_not_called()


async def test_expense_income_with_amount(bot_deps: BotDeps) -> None:
    meta = make_meta(
        intent="expense",
        expense={"action": "income", "amount": 2100.0, "label": "salaire", "starts_cycle": True},
    )
    await handle_expense_side_effect(meta, bot_deps)
    bot_deps.expenses.start_cycle.assert_awaited_once()
    bot_deps.expenses.add_income.assert_awaited_once()
    _, kwargs = bot_deps.expenses.add_income.call_args
    assert kwargs["amount_cents"] == 210000


async def test_expense_income_no_amount_no_cycle_skipped(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="expense", expense={"action": "income", "amount": None})
    await handle_expense_side_effect(meta, bot_deps)
    bot_deps.expenses.start_cycle.assert_not_called()
    bot_deps.expenses.add_income.assert_not_called()


async def test_expense_tick_missing_key_skipped(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="expense", expense={"action": "tick_recurring", "recurring_key": None})
    await handle_expense_side_effect(meta, bot_deps)
    bot_deps.expenses.tick_recurring_once.assert_not_called()


async def test_expense_tick_unknown_key_skipped(
    bot_deps: BotDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = MagicMock()
    cfg.find.return_value = None
    monkeypatch.setattr("bot.finance.config.extract_finance_config", lambda _data: cfg)
    meta = make_meta(
        intent="expense", expense={"action": "tick_recurring", "recurring_key": "inconnu"}
    )
    await handle_expense_side_effect(meta, bot_deps)
    bot_deps.expenses.tick_recurring_once.assert_not_called()


async def test_expense_tick_success_uses_yaml_amount(
    bot_deps: BotDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = MagicMock(
        key="loyer", label="Loyer", amount_cents=85000, kind="charge", category="logement"
    )
    cfg = MagicMock()
    cfg.find.return_value = item
    monkeypatch.setattr("bot.finance.config.extract_finance_config", lambda _data: cfg)
    meta = make_meta(
        intent="expense",
        expense={"action": "tick_recurring", "recurring_key": "loyer", "amount": None},
    )
    await handle_expense_side_effect(meta, bot_deps)
    bot_deps.expenses.tick_recurring_once.assert_awaited_once()
    _, kwargs = bot_deps.expenses.tick_recurring_once.call_args
    assert kwargs["amount_cents"] == 85000  # montant YAML, pas d'override


async def test_expense_tick_amount_override(
    bot_deps: BotDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = MagicMock(
        key="pel", label="PEL", amount_cents=10000, kind="epargne", category="placement"
    )
    cfg = MagicMock()
    cfg.find.return_value = item
    monkeypatch.setattr("bot.finance.config.extract_finance_config", lambda _data: cfg)
    meta = make_meta(
        intent="expense",
        expense={"action": "tick_recurring", "recurring_key": "pel", "amount": 11.0},
    )
    await handle_expense_side_effect(meta, bot_deps)
    _, kwargs = bot_deps.expenses.tick_recurring_once.call_args
    assert kwargs["amount_cents"] == 1100  # override utilisateur


async def test_expense_tick_duplicate_ignored(
    bot_deps: BotDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = MagicMock(
        key="loyer", label="Loyer", amount_cents=85000, kind="charge", category="logement"
    )
    cfg = MagicMock()
    cfg.find.return_value = item
    monkeypatch.setattr("bot.finance.config.extract_finance_config", lambda _data: cfg)
    bot_deps.expenses.tick_recurring_once = AsyncMock(return_value=None)  # déjà pointé
    meta = make_meta(
        intent="expense", expense={"action": "tick_recurring", "recurring_key": "loyer"}
    )
    # Ne doit pas lever malgré le tick None.
    await handle_expense_side_effect(meta, bot_deps)
    bot_deps.expenses.tick_recurring_once.assert_awaited_once()


async def test_expense_tick_config_failure_is_soft(
    bot_deps: BotDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_data: object) -> None:
        raise ValueError("YAML cassé")

    monkeypatch.setattr("bot.finance.config.extract_finance_config", _boom)
    meta = make_meta(
        intent="expense", expense={"action": "tick_recurring", "recurring_key": "loyer"}
    )
    await handle_expense_side_effect(meta, bot_deps)
    bot_deps.expenses.tick_recurring_once.assert_not_called()


# --- euros_to_cents ----------------------------------------------------------


def test_euros_to_cents() -> None:
    assert euros_to_cents(None) is None
    assert euros_to_cents(0) is None
    assert euros_to_cents(-5.0) is None
    assert euros_to_cents(12.5) == 1250
    assert euros_to_cents(0.1) == 10  # arrondi propre


# --- safe_open_worries / safe_pending_recurring ------------------------------


async def test_safe_open_worries_success(bot_deps: BotDeps) -> None:
    bot_deps.thoughts.list_open = AsyncMock(return_value=[_thought(1), _thought(2)])
    result = await safe_open_worries(bot_deps)
    assert len(result) == 2


async def test_safe_open_worries_failure_returns_empty(bot_deps: BotDeps) -> None:
    bot_deps.thoughts.list_open = AsyncMock(side_effect=RuntimeError("db down"))
    assert await safe_open_worries(bot_deps) == ()


async def test_safe_pending_recurring_failure_returns_empty(bot_deps: BotDeps) -> None:
    bot_deps.expenses.list_for_cycle = AsyncMock(side_effect=RuntimeError("db down"))
    assert await safe_pending_recurring(bot_deps) == ()


async def test_safe_pending_recurring_not_configured(
    bot_deps: BotDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = MagicMock()
    cfg.is_configured = False
    monkeypatch.setattr("bot.finance.config.extract_finance_config", lambda _data: cfg)
    assert await safe_pending_recurring(bot_deps) == ()
