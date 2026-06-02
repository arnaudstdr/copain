"""Tests unitaires du parsing de dates FR dans le pipeline."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bot.pipeline.core import _normalize_fr_time_words, _parse_due, _parse_when_to_date


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


def test_parse_when_to_date_prefers_past() -> None:
    """Une saisie financière ne doit jamais être projetée dans le futur.

    Régression du bug d'ancre de cycle en 2027 : « le 29 » un 29 du mois
    était poussé à l'année suivante par la préférence futur.
    """
    tz = ZoneInfo("Europe/Paris")
    today = datetime.now(tz).date()
    for day in (1, 5, 15, 28):
        resolved = _parse_when_to_date(f"le {day}", "Europe/Paris")
        assert resolved <= today


def test_parse_when_to_date_keeps_requested_day_in_previous_month() -> None:
    """« le N » résolu dans le futur recule d'un mois en conservant le jour.

    dateparser ignore PREFER_DATES_FROM="past" pour les jours du mois : le
    recul est fait manuellement par `_parse_when_to_date`. Le jour demandé
    doit être conservé (modulo clamp fin de mois) et la date rester à moins
    d'un mois dans le passé.
    """
    tz = ZoneInfo("Europe/Paris")
    today = datetime.now(tz).date()
    for day in (1, 5, 15, 28):
        resolved = _parse_when_to_date(f"le {day}", "Europe/Paris")
        assert resolved <= today
        assert resolved.day == day
        assert (today - resolved).days < 32


def test_parse_when_to_date_defaults_to_today_when_empty() -> None:
    tz = ZoneInfo("Europe/Paris")
    assert _parse_when_to_date(None, "Europe/Paris") == datetime.now(tz).date()
