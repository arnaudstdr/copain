"""Décharge cognitive : dépôts de pensées (intent `depot`).

Le module expose le modèle `Thought` (table SQLite partageant la `Base`
SQLAlchemy avec `tasks` / `feeds` / `notifications`) et `ThoughtManager`
pour persister et lister les dépôts. Le pipeline `bot.pipeline` route
l'intent `depot` du LLM vers `ThoughtManager.create` et indexe en
parallèle le contenu dans ChromaDB via `MemoryManager.store_depot` pour
permettre une future détection de boucles cognitives.
"""

from __future__ import annotations

from bot.thoughts.manager import ThoughtManager
from bot.thoughts.models import THOUGHT_KINDS, Thought, ThoughtKind

__all__ = ["THOUGHT_KINDS", "Thought", "ThoughtKind", "ThoughtManager"]
