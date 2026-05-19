"""Tests unitaires du parsing de dates FR dans le pipeline."""

from __future__ import annotations

from bot.pipeline import _normalize_fr_time_words, _parse_due


def test_normalize_midi_and_minuit() -> None:
    assert _normalize_fr_time_words("demain midi") == "demain 12:00"
    # "ce soir" est substitué à "aujourd'hui" (dateparser gère bien cette expression).
    assert _normalize_fr_time_words("ce soir minuit") == "aujourd'hui 00:00"
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


def test_normalize_hour_markers() -> None:
    # "Xh" et "XhYY" → "HH:MM" pour empêcher dateparser de lire une durée.
    assert _normalize_fr_time_words("demain à 7h") == "demain à 07:00"
    assert _normalize_fr_time_words("demain 7h30") == "demain 07:30"
    assert _normalize_fr_time_words("vendredi 19h00") == "vendredi 19:00"
    # Pas de transformation derrière un connecteur de durée.
    assert _normalize_fr_time_words("dans 7h") == "dans 7h"
    assert _normalize_fr_time_words("il y a 2h") == "il y a 2h"


def test_parse_due_handles_explicit_hour() -> None:
    # Bug historique : "demain à 7h" était interprété comme "maintenant + 7h"
    # → l'heure de rappel collait à l'heure d'envoi du message au lieu de 07:00.
    result = _parse_due("demain à 7h", "Europe/Paris")
    assert result is not None
    assert result.hour == 7
    assert result.minute == 0


def test_parse_due_handles_hour_with_minutes() -> None:
    result = _parse_due("demain 7h30", "Europe/Paris")
    assert result is not None
    assert result.hour == 7
    assert result.minute == 30
