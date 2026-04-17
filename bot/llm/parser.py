"""Extraction et validation du bloc <meta> JSON produit par le LLM."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict

META_PATTERN = re.compile(r"<meta>\s*(\{.*?\})\s*</meta>", re.DOTALL)

Intent = Literal["answer", "task", "search", "memory"]
VALID_INTENTS: frozenset[str] = frozenset({"answer", "task", "search", "memory"})


class TaskMeta(TypedDict):
    content: str | None
    due_str: str | None


class Meta(TypedDict):
    intent: Intent
    store_memory: bool
    memory_content: str | None
    task: TaskMeta
    search_query: str | None


class MetaParseError(ValueError):
    """Levée si le bloc <meta> est absent, mal formé, ou de schéma invalide."""


def extract_meta(raw: str) -> tuple[str, Meta]:
    """Extrait le bloc <meta>, le parse, et retourne (texte_propre, meta_validée).

    Lève MetaParseError si le bloc est absent, non parsable en JSON, ou si le
    schéma est invalide. L'appelant doit gérer ce cas (réponse par défaut).
    """
    match = META_PATTERN.search(raw)
    if not match:
        raise MetaParseError("Bloc <meta> absent de la réponse du LLM")

    json_str = match.group(1)
    try:
        data: Any = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise MetaParseError(f"JSON du bloc <meta> invalide : {exc.msg}") from exc

    meta = _validate(data)
    clean_text = META_PATTERN.sub("", raw).strip()
    return clean_text, meta


def _validate(data: Any) -> Meta:
    if not isinstance(data, dict):
        raise MetaParseError("Le bloc <meta> doit être un objet JSON")

    intent = data.get("intent")
    if intent not in VALID_INTENTS:
        raise MetaParseError(f"intent invalide : {intent!r}")

    store_memory = data.get("store_memory")
    if not isinstance(store_memory, bool):
        raise MetaParseError("store_memory doit être un booléen")

    memory_content = data.get("memory_content")
    if memory_content is not None and not isinstance(memory_content, str):
        raise MetaParseError("memory_content doit être une chaîne ou null")

    task_raw = data.get("task") or {"content": None, "due_str": None}
    if not isinstance(task_raw, dict):
        raise MetaParseError("task doit être un objet ou null")
    task: TaskMeta = {
        "content": _opt_str(task_raw.get("content"), "task.content"),
        "due_str": _opt_str(task_raw.get("due_str"), "task.due_str"),
    }

    search_query = _opt_str(data.get("search_query"), "search_query")

    return Meta(
        intent=intent,  # type: ignore[typeddict-item]
        store_memory=store_memory,
        memory_content=memory_content,
        task=task,
        search_query=search_query,
    )


def _opt_str(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MetaParseError(f"{field} doit être une chaîne ou null")
    return value
