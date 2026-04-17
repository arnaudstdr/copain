"""Tests du parser <meta>."""

from __future__ import annotations

import pytest

from bot.llm.parser import MetaParseError, extract_meta


def test_extract_meta_task_intent(sample_llm_response: str) -> None:
    text, meta = extract_meta(sample_llm_response)
    assert "demain à 18h" in text
    assert "<meta>" not in text
    assert meta["intent"] == "task"
    assert meta["store_memory"] is True
    assert meta["task"]["content"] == "arroser les plantes"
    assert meta["task"]["due_str"] == "demain 18h"
    assert meta["search_query"] is None


def test_extract_meta_answer_without_side_effects() -> None:
    raw = """\
Bonjour !
<meta>
{
  "intent": "answer",
  "store_memory": false,
  "memory_content": null,
  "task": {"content": null, "due_str": null},
  "search_query": null
}
</meta>"""
    text, meta = extract_meta(raw)
    assert text.strip() == "Bonjour !"
    assert meta["intent"] == "answer"
    assert meta["store_memory"] is False
    assert meta["memory_content"] is None


def test_extract_meta_search_intent() -> None:
    raw = """\
Je cherche.
<meta>
{
  "intent": "search",
  "store_memory": false,
  "memory_content": null,
  "task": {"content": null, "due_str": null},
  "search_query": "météo Paris demain"
}
</meta>"""
    _, meta = extract_meta(raw)
    assert meta["intent"] == "search"
    assert meta["search_query"] == "météo Paris demain"


def test_extract_meta_missing_block_raises() -> None:
    with pytest.raises(MetaParseError, match="absent"):
        extract_meta("Juste du texte sans meta.")


def test_extract_meta_invalid_json_raises() -> None:
    raw = "<meta>{not valid json}</meta>"
    with pytest.raises(MetaParseError, match="JSON"):
        extract_meta(raw)


def test_extract_meta_invalid_intent_raises() -> None:
    raw = """<meta>{"intent": "banana", "store_memory": false, "memory_content": null,
"task": {"content": null, "due_str": null}, "search_query": null}</meta>"""
    with pytest.raises(MetaParseError, match="intent"):
        extract_meta(raw)


def test_extract_meta_store_memory_must_be_bool() -> None:
    raw = """<meta>{"intent": "answer", "store_memory": "yes", "memory_content": null,
"task": {"content": null, "due_str": null}, "search_query": null}</meta>"""
    with pytest.raises(MetaParseError, match="store_memory"):
        extract_meta(raw)
