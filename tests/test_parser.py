"""Tests du parser <meta>."""

from __future__ import annotations

import pytest

from bot.llm.parser import MetaParseError, MetaStreamFilter, extract_meta


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


def test_extract_meta_depot_worry() -> None:
    raw = """\
Noté.
<meta>
{
  "intent": "depot",
  "store_memory": false,
  "memory_content": null,
  "task": {"content": null, "due_str": null},
  "feed": {"action": null, "name": null, "url": null},
  "event": {"action": null, "title": null, "start_str": null, "end_str": null,
            "location": null, "description": null, "range_str": null},
  "fuel": {"fuel_type": null, "radius_km": null, "location": null},
  "weather": {"location": null, "when": null},
  "depot": {"content": "j'ai peur pour les finances de mon fils", "kind": "worry"},
  "search_query": null
}
</meta>"""
    text, meta = extract_meta(raw)
    assert text.strip() == "Noté."
    assert meta["intent"] == "depot"
    assert meta["depot"]["content"] == "j'ai peur pour les finances de mon fils"
    assert meta["depot"]["kind"] == "worry"


def test_extract_meta_depot_idea() -> None:
    raw = """<meta>{"intent":"depot","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":null,"title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":null},
"fuel":{"fuel_type":null,"radius_km":null,"location":null},
"weather":{"location":null,"when":null},
"depot":{"content":"refactorer le pipeline","kind":"idea"},
"search_query":null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["depot"]["kind"] == "idea"


def test_extract_meta_depot_invalid_kind_raises() -> None:
    raw = """<meta>{"intent":"depot","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":null,"title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":null},
"fuel":{"fuel_type":null,"radius_km":null,"location":null},
"weather":{"location":null,"when":null},
"depot":{"content":"x","kind":"sadness"},
"search_query":null}</meta>"""
    with pytest.raises(MetaParseError, match=r"depot\.kind"):
        extract_meta(raw)


def test_extract_meta_depot_optional_in_old_format() -> None:
    """Rétrocompat : si le LLM oublie depot, défauts à None (et action=add)."""
    raw = """<meta>{"intent": "answer", "store_memory": false, "memory_content": null,
"task": {"content": null, "due_str": null},
"feed": {"action": null, "name": null, "url": null},
"search_query": null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["depot"]["content"] is None
    assert meta["depot"]["kind"] is None
    assert meta["depot"]["action"] == "add"
    assert meta["depot"]["thought_id"] is None


def test_extract_meta_depot_action_defaults_to_add_when_missing() -> None:
    """Rétrocompat : un bloc depot sans `action` (ancien prompt) → action=add."""
    raw = """<meta>{"intent":"depot","store_memory":false,"memory_content":null,
"depot":{"content":"une pensée","kind":"note"},
"search_query":null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["depot"]["action"] == "add"
    assert meta["depot"]["thought_id"] is None


def test_extract_meta_depot_close_with_thought_id() -> None:
    raw = """<meta>{"intent":"depot","store_memory":false,"memory_content":null,
"depot":{"content":null,"kind":null,"action":"close","thought_id":12},
"search_query":null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["depot"]["action"] == "close"
    assert meta["depot"]["thought_id"] == 12


def test_extract_meta_depot_thought_id_str_numeric_accepted() -> None:
    """Le LLM peut émettre l'id en chaîne ("12") : converti en int."""
    raw = """<meta>{"intent":"depot","store_memory":false,"memory_content":null,
"depot":{"content":null,"kind":null,"action":"close","thought_id":"12"},
"search_query":null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["depot"]["thought_id"] == 12


def test_extract_meta_depot_invalid_action_raises() -> None:
    raw = """<meta>{"intent":"depot","store_memory":false,"memory_content":null,
"depot":{"content":null,"kind":null,"action":"delete","thought_id":12},
"search_query":null}</meta>"""
    with pytest.raises(MetaParseError, match=r"depot\.action"):
        extract_meta(raw)


def test_extract_meta_depot_thought_id_not_numeric_raises() -> None:
    raw = """<meta>{"intent":"depot","store_memory":false,"memory_content":null,
"depot":{"content":null,"kind":null,"action":"close","thought_id":"abc"},
"search_query":null}</meta>"""
    with pytest.raises(MetaParseError, match=r"depot\.thought_id"):
        extract_meta(raw)


def test_extract_meta_depot_thought_id_bool_rejected() -> None:
    """`true` est un int en Python : il doit quand même être rejeté."""
    raw = """<meta>{"intent":"depot","store_memory":false,"memory_content":null,
"depot":{"content":null,"kind":null,"action":"close","thought_id":true},
"search_query":null}</meta>"""
    with pytest.raises(MetaParseError, match=r"depot\.thought_id"):
        extract_meta(raw)


def test_extract_meta_expense_spend() -> None:
    raw = """\
Noté.
<meta>
{
  "intent": "expense",
  "store_memory": false,
  "memory_content": null,
  "task": {"content": null, "due_str": null},
  "feed": {"action": null, "name": null, "url": null},
  "event": {"action": null, "title": null, "start_str": null, "end_str": null,
            "location": null, "description": null, "range_str": null, "calendar_name": null},
  "fuel": {"fuel_type": null, "radius_km": null, "location": null},
  "weather": {"location": null, "when": null},
  "depot": {"content": null, "kind": null},
  "expense": {"action": "spend", "amount": 27, "label": "pharmacie",
              "category": "santé", "recurring_key": null, "when": null},
  "search_query": null
}
</meta>"""
    text, meta = extract_meta(raw)
    assert text.strip() == "Noté."
    assert meta["intent"] == "expense"
    assert meta["expense"]["action"] == "spend"
    assert meta["expense"]["amount"] == 27.0
    assert meta["expense"]["label"] == "pharmacie"
    assert meta["expense"]["category"] == "santé"
    assert meta["expense"]["recurring_key"] is None


def test_extract_meta_expense_income() -> None:
    raw = """<meta>{"intent":"expense","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":null,"title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":null,"calendar_name":null},
"fuel":{"fuel_type":null,"radius_km":null,"location":null},
"weather":{"location":null,"when":null},
"depot":{"content":null,"kind":null},
"expense":{"action":"income","amount":2500,"label":"salaire mai",
"category":null,"recurring_key":null,"when":null},
"search_query":null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["expense"]["action"] == "income"
    assert meta["expense"]["amount"] == 2500.0
    assert meta["expense"]["label"] == "salaire mai"
    # Absent du JSON → défaut False (rétro-compat).
    assert meta["expense"]["starts_cycle"] is False


def test_extract_meta_expense_salary_starts_cycle() -> None:
    raw = """<meta>{"intent":"expense","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":null,"title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":null,"calendar_name":null},
"fuel":{"fuel_type":null,"radius_km":null,"location":null},
"weather":{"location":null,"when":null},
"depot":{"content":null,"kind":null},
"expense":{"action":"income","amount":2500,"label":"salaire",
"category":null,"recurring_key":null,"when":null,"shared":false,"starts_cycle":true},
"search_query":null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["expense"]["action"] == "income"
    assert meta["expense"]["starts_cycle"] is True


def test_extract_meta_expense_starts_cycle_must_be_bool() -> None:
    raw = """<meta>{"intent":"expense","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":null,"title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":null,"calendar_name":null},
"fuel":{"fuel_type":null,"radius_km":null,"location":null},
"weather":{"location":null,"when":null},
"depot":{"content":null,"kind":null},
"expense":{"action":"income","amount":2500,"label":"salaire",
"category":null,"recurring_key":null,"when":null,"starts_cycle":"oui"},
"search_query":null}</meta>"""
    with pytest.raises(MetaParseError, match=r"expense\.starts_cycle"):
        extract_meta(raw)


def test_extract_meta_expense_tick_recurring() -> None:
    raw = """<meta>{"intent":"expense","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":null,"title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":null,"calendar_name":null},
"fuel":{"fuel_type":null,"radius_km":null,"location":null},
"weather":{"location":null,"when":null},
"depot":{"content":null,"kind":null},
"expense":{"action":"tick_recurring","amount":800,"label":"Loyer appartement",
"category":null,"recurring_key":"loyer","when":null},
"search_query":null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["expense"]["action"] == "tick_recurring"
    assert meta["expense"]["recurring_key"] == "loyer"


def test_extract_meta_expense_invalid_action_raises() -> None:
    raw = """<meta>{"intent":"expense","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":null,"title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":null,"calendar_name":null},
"fuel":{"fuel_type":null,"radius_km":null,"location":null},
"weather":{"location":null,"when":null},
"depot":{"content":null,"kind":null},
"expense":{"action":"banana","amount":1,"label":"x",
"category":null,"recurring_key":null,"when":null},
"search_query":null}</meta>"""
    with pytest.raises(MetaParseError, match=r"expense\.action"):
        extract_meta(raw)


def test_extract_meta_expense_negative_amount_rejected() -> None:
    raw = """<meta>{"intent":"expense","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":null,"title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":null,"calendar_name":null},
"fuel":{"fuel_type":null,"radius_km":null,"location":null},
"weather":{"location":null,"when":null},
"depot":{"content":null,"kind":null},
"expense":{"action":"spend","amount":-10,"label":"x",
"category":null,"recurring_key":null,"when":null},
"search_query":null}</meta>"""
    with pytest.raises(MetaParseError, match=r"expense\.amount.+positif"):
        extract_meta(raw)


def test_extract_meta_expense_optional_in_old_format() -> None:
    """Rétrocompat : si le LLM oublie le bloc expense, défauts à None."""
    raw = """<meta>{"intent": "answer", "store_memory": false, "memory_content": null,
"task": {"content": null, "due_str": null},
"feed": {"action": null, "name": null, "url": null},
"search_query": null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["expense"]["action"] is None
    assert meta["expense"]["amount"] is None
    assert meta["expense"]["shared"] is False


def test_extract_meta_expense_shared_default_false_when_missing() -> None:
    """Rétrocompat : un bloc expense sans champ shared → shared=False."""
    raw = """<meta>{"intent":"expense","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":null,"title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":null,"calendar_name":null},
"fuel":{"fuel_type":null,"radius_km":null,"location":null},
"weather":{"location":null,"when":null},
"depot":{"content":null,"kind":null},
"expense":{"action":"spend","amount":27,"label":"pharmacie",
"category":"santé","recurring_key":null,"when":null},
"search_query":null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["expense"]["shared"] is False


def test_extract_meta_expense_shared_true() -> None:
    """shared=true bien extrait quand le LLM le précise."""
    raw = """<meta>{"intent":"expense","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":null,"title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":null,"calendar_name":null},
"fuel":{"fuel_type":null,"radius_km":null,"location":null},
"weather":{"location":null,"when":null},
"depot":{"content":null,"kind":null},
"expense":{"action":"spend","amount":30,"label":"Lidl",
"category":"nourriture","recurring_key":null,"when":null,"shared":true},
"search_query":null}</meta>"""
    _, meta = extract_meta(raw)
    assert meta["expense"]["shared"] is True
    assert meta["expense"]["category"] == "nourriture"


def test_extract_meta_expense_shared_non_bool_rejected() -> None:
    """Garde-fou : shared doit être un booléen, pas une chaîne ou un nombre."""
    raw = """<meta>{"intent":"expense","store_memory":false,"memory_content":null,
"task":{"content":null,"due_str":null},
"feed":{"action":null,"name":null,"url":null},
"event":{"action":null,"title":null,"start_str":null,"end_str":null,
"location":null,"description":null,"range_str":null,"calendar_name":null},
"fuel":{"fuel_type":null,"radius_km":null,"location":null},
"weather":{"location":null,"when":null},
"depot":{"content":null,"kind":null},
"expense":{"action":"spend","amount":30,"label":"x",
"category":null,"recurring_key":null,"when":null,"shared":"true"},
"search_query":null}</meta>"""
    with pytest.raises(MetaParseError, match=r"expense\.shared"):
        extract_meta(raw)


# --- MetaStreamFilter --------------------------------------------------------


_META_JSON = (
    '{"intent": "answer", "store_memory": false, "memory_content": null,'
    ' "task": {"content": null, "due_str": null}, "search_query": null}'
)


def test_stream_filter_marker_split_across_chunks() -> None:
    """Le marqueur <meta> coupé entre deux chunks ne doit jamais être émis."""
    f = MetaStreamFilter()
    out = f.feed("Bonjour !")
    out += f.feed("\n\n<me")
    out += f.feed(f"ta>{_META_JSON}</meta>")
    out += f.flush()
    assert out == "Bonjour !"
    assert f.meta_started is True


def test_stream_filter_raw_accumulates_full_response() -> None:
    """`raw` doit contenir la réponse brute complète, bloc <meta> inclus."""
    chunks = ["Salut.", "\n<meta>", _META_JSON, "</meta>"]
    f = MetaStreamFilter()
    for c in chunks:
        f.feed(c)
    assert f.raw == "".join(chunks)
    text, meta = extract_meta(f.raw)
    assert text == "Salut."
    assert meta["intent"] == "answer"


def test_stream_filter_legitimate_angle_bracket_is_emitted() -> None:
    """Un `<` ou un préfixe partiel (<met…) qui n'aboutit pas reste visible."""
    f = MetaStreamFilter()
    out = f.feed("a < b et la <met")
    out += f.feed("hode> marche")
    out += f.flush()
    assert out == "a < b et la <methode> marche"
    assert f.meta_started is False


def test_stream_filter_no_meta_flushes_remaining_text() -> None:
    f = MetaStreamFilter()
    out = f.feed("Texte sans bloc meta.")
    out += f.flush()
    assert out == "Texte sans bloc meta."


def test_stream_filter_whitespace_before_meta_not_emitted() -> None:
    """Les \\n\\n qui précèdent le bloc <meta> sont retenus (cohérent avec .strip())."""
    f = MetaStreamFilter()
    out = f.feed("Fin de réponse.\n\n")
    out += f.feed(f"<meta>{_META_JSON}</meta>")
    out += f.flush()
    assert out == "Fin de réponse."


def test_stream_filter_nothing_emitted_after_meta_started() -> None:
    f = MetaStreamFilter()
    f.feed(f"Texte.\n<meta>{_META_JSON}")
    assert f.feed("</meta>") == ""
    assert f.feed("du bruit après") == ""
    assert f.flush() == ""
