"""Tests unitaires du parsing de dates FR dans handlers."""

from __future__ import annotations

from bot.handlers import _normalize_fr_time_words, _parse_due


def test_normalize_midi_and_minuit() -> None:
    assert _normalize_fr_time_words("demain midi") == "demain 12:00"
    assert _normalize_fr_time_words("ce soir minuit") == "ce soir 00:00"
    assert _normalize_fr_time_words("Midi pile") == "12:00 pile"


def test_parse_due_handles_midi() -> None:
    result = _parse_due("demain midi", "Europe/Paris")
    assert result is not None
    assert result.hour == 12
    assert result.minute == 0
    assert result.tzinfo is not None


def test_parse_due_handles_minuit() -> None:
    result = _parse_due("minuit", "Europe/Paris")
    assert result is not None
    assert result.hour == 0
    assert result.minute == 0


def test_parse_due_none_when_empty() -> None:
    assert _parse_due(None, "Europe/Paris") is None
    assert _parse_due("", "Europe/Paris") is None
