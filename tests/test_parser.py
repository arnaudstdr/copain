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


def test_extract_meta_feed_add() -> None:
    raw = """\
OK, je l'ajoute.
<meta>
{
  "intent": "feed",
  "store_memory": false,
  "memory_content": null,
  "task": {"content": null, "due_str": null},
  "feed": {"action": "add", "name": "The Verge", "url": "https://www.theverge.com/rss/index.xml"},
  "search_query": null
}
</meta>"""
    text, meta = extract_meta(raw)
    assert text.strip() == "OK, je l'ajoute."
    assert meta["intent"] == "feed"
    assert meta["feed"]["action"] == "add"
    assert meta["feed"]["name"] == "The Verge"
    assert meta["feed"]["url"] == "https://www.theverge.com/rss/index.xml"


def test_extract_meta_feed_summarize() -> None:
    raw = """<meta>{"intent": "feed", "store_memory": false, "memory_content": null,
"task": {"content": null, "due_str": null},
"feed": {"action": "summarize", "name": "ZDNet", "url": null},
"search_query": null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["feed"]["action"] == "summarize"
    assert meta["feed"]["name"] == "ZDNet"


def test_extract_meta_feed_action_invalid_raises() -> None:
    raw = """<meta>{"intent": "feed", "store_memory": false, "memory_content": null,
"task": {"content": null, "due_str": null},
"feed": {"action": "dance", "name": null, "url": null},
"search_query": null}</meta>"""
    with pytest.raises(MetaParseError, match=r"feed\.action"):
        extract_meta(raw)


def test_extract_meta_feed_optional_in_old_format() -> None:
    """Rétrocompat : si le LLM oublie le champ feed, on tolère via {action: null, ...}."""
    raw = """<meta>{"intent": "answer", "store_memory": false, "memory_content": null,
"task": {"content": null, "due_str": null}, "search_query": null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["feed"]["action"] is None
    assert meta["feed"]["name"] is None
    assert meta["feed"]["url"] is None


def test_extract_meta_event_create() -> None:
    raw = """\
OK, je l'ajoute au calendrier.
<meta>
{
  "intent": "event",
  "store_memory": false,
  "memory_content": null,
  "task": {"content": null, "due_str": null},
  "feed": {"action": null, "name": null, "url": null},
  "event": {
    "action": "create",
    "title": "RDV dentiste",
    "start_str": "mardi 15h",
    "end_str": null,
    "location": null,
    "description": null,
    "range_str": null
  },
  "search_query": null
}
</meta>"""
    text, meta = extract_meta(raw)
    assert text.strip() == "OK, je l'ajoute au calendrier."
    assert meta["intent"] == "event"
    assert meta["event"]["action"] == "create"
    assert meta["event"]["title"] == "RDV dentiste"
    assert meta["event"]["start_str"] == "mardi 15h"


def test_extract_meta_event_list() -> None:
    raw = """<meta>{"intent":"event","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":"list","title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":"cette semaine"},
"search_query":null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["event"]["action"] == "list"
    assert meta["event"]["range_str"] == "cette semaine"


def test_extract_meta_event_invalid_action() -> None:
    raw = """<meta>{"intent":"event","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":"delete","title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":null},
"search_query":null}</meta>"""
    with pytest.raises(MetaParseError, match=r"event\.action"):
        extract_meta(raw)


def test_extract_meta_event_optional_in_old_format() -> None:
    """Rétrocompat : si le LLM oublie le champ event, on tolère via {action: null, ...}."""
    raw = """<meta>{"intent": "answer", "store_memory": false, "memory_content": null,
"task": {"content": null, "due_str": null},
"feed": {"action": null, "name": null, "url": null},
"search_query": null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["event"]["action"] is None
    assert meta["event"]["title"] is None
    assert meta["event"]["range_str"] is None


def test_extract_meta_fuel_full() -> None:
    raw = """\
OK, je cherche.
<meta>
{
  "intent": "fuel",
  "store_memory": false,
  "memory_content": null,
  "task": {"content": null, "due_str": null},
  "feed": {"action": null, "name": null, "url": null},
  "event": {"action": null, "title": null, "start_str": null, "end_str": null,
            "location": null, "description": null, "range_str": null},
  "fuel": {"fuel_type": "sp98", "radius_km": 5, "location": "Colmar"},
  "search_query": null
}
</meta>"""
    text, meta = extract_meta(raw)
    assert text.strip() == "OK, je cherche."
    assert meta["intent"] == "fuel"
    assert meta["fuel"]["fuel_type"] == "sp98"
    assert meta["fuel"]["radius_km"] == 5.0
    assert meta["fuel"]["location"] == "Colmar"


def test_extract_meta_fuel_optional_in_old_format() -> None:
    """Rétrocompat : si le LLM oublie le champ fuel, défauts à None."""
    raw = """<meta>{"intent": "answer", "store_memory": false, "memory_content": null,
"task": {"content": null, "due_str": null},
"feed": {"action": null, "name": null, "url": null},
"search_query": null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["fuel"]["fuel_type"] is None
    assert meta["fuel"]["radius_km"] is None
    assert meta["fuel"]["location"] is None


def test_extract_meta_fuel_radius_invalid_raises() -> None:
    raw = """<meta>{"intent": "fuel", "store_memory": false, "memory_content": null,
"task": {"content": null, "due_str": null},
"feed": {"action": null, "name": null, "url": null},
"fuel": {"fuel_type": "gazole", "radius_km": "abc", "location": null},
"search_query": null}</meta>"""
    with pytest.raises(MetaParseError, match=r"fuel\.radius_km"):
        extract_meta(raw)


def test_extract_meta_weather_full() -> None:
    raw = """\
Je regarde.
<meta>
{
  "intent": "weather",
  "store_memory": false,
  "memory_content": null,
  "task": {"content": null, "due_str": null},
  "feed": {"action": null, "name": null, "url": null},
  "event": {"action": null, "title": null, "start_str": null, "end_str": null,
            "location": null, "description": null, "range_str": null},
  "fuel": {"fuel_type": null, "radius_km": null, "location": null},
  "weather": {"location": "Strasbourg", "when": "ce weekend"},
  "search_query": null
}
</meta>"""
    text, meta = extract_meta(raw)
    assert text.strip() == "Je regarde."
    assert meta["intent"] == "weather"
    assert meta["weather"]["location"] == "Strasbourg"
    assert meta["weather"]["when"] == "ce weekend"


def test_extract_meta_weather_optional_in_old_format() -> None:
    """Rétrocompat : si le LLM oublie weather, défauts à None."""
    raw = """<meta>{"intent": "answer", "store_memory": false, "memory_content": null,
"task": {"content": null, "due_str": null},
"feed": {"action": null, "name": null, "url": null},
"search_query": null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["weather"]["location"] is None
    assert meta["weather"]["when"] is None


def test_extract_meta_weather_location_wrong_type_raises() -> None:
    raw = """<meta>{"intent": "weather", "store_memory": false, "memory_content": null,
"task": {"content": null, "due_str": null},
"feed": {"action": null, "name": null, "url": null},
"fuel": {"fuel_type": null, "radius_km": null, "location": null},
"weather": {"location": 123, "when": null},
"search_query": null}</meta>"""
    with pytest.raises(MetaParseError, match=r"weather\.location"):
        extract_meta(raw)
