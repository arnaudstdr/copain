"""Parsing de dates FR : expressions naturelles → datetime / date / plages.

Regroupe les subtilités critiques du parsing côté français : préférence
passé/futur selon le contexte (tâches vs saisies financières), normalisation
des mots que dateparser ignore (« midi », « minuit », « Xh ») et recul de
mois manuel pour les expressions jour-du-mois. Module feuille du package —
aucune dépendance interne à `bot.pipeline`.
"""

from __future__ import annotations

import calendar as _calendar
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import dateparser


def parse_due(due_str: str | None, tz_name: str, prefer: str = "future") -> datetime | None:
    """Parse une expression FR et retourne un datetime aware dans la timezone voulue.

    Sans `TIMEZONE` + `RETURN_AS_TIMEZONE_AWARE`, dateparser renvoie un datetime
    naïf, qu'APScheduler interprète en UTC → décalage en prod (le container est
    souvent en UTC).

    `prefer` pilote `PREFER_DATES_FROM` : "future" pour les tâches/RDV (par
    défaut), "past" pour les saisies financières (cf. `parse_when_to_date`).

    dateparser FR ne reconnaît pas « midi » / « minuit » : on les pré-normalise.
    """
    if not due_str:
        return None
    normalized = normalize_fr_time_words(due_str)
    parsed = dateparser.parse(
        normalized,
        languages=["fr"],
        settings={
            "PREFER_DATES_FROM": prefer,
            "TIMEZONE": tz_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
    return parsed


def parse_when_to_date(when_str: str | None, tz_name: str) -> date:
    """Parse une expression FR ('hier', 'le 5') en `date`, défaut = aujourd'hui.

    Préférence **passé** : une saisie financière (dépense, revenu, salaire,
    pointage) décrit toujours un événement déjà survenu. Sans ça, une date
    comme « le 29 » un 29 du mois serait poussée à l'année suivante par la
    préférence futur (bug d'ancre de cycle en 2027).

    dateparser (1.4) ignore `PREFER_DATES_FROM="past"` pour les expressions
    jour-du-mois (« le 5 » résout toujours au 5 du mois courant, même futur) :
    on recule donc manuellement d'un mois toute date résolue dans le futur,
    en clampant le jour à la fin du mois cible (« le 31 » → 30/04, etc.).
    """
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    if not when_str:
        return today
    parsed = parse_due(when_str, tz_name, prefer="past")
    if parsed is None:
        return today
    resolved = parsed.astimezone(tz).date()
    if resolved > today:
        year, month = (
            (resolved.year, resolved.month - 1) if resolved.month > 1 else (resolved.year - 1, 12)
        )
        last_day = _calendar.monthrange(year, month)[1]
        resolved = date(year, month, min(resolved.day, last_day))
    return resolved


def parse_range(range_str: str | None, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Convertit une expression FR de plage en (start, end) timezone-aware.

    Par défaut (range_str absent) : 7 jours à venir. Sinon on utilise dateparser
    pour identifier un repère, et on étend symboliquement 'aujourd'hui', 'demain',
    'cette semaine', 'ce mois', etc.
    """
    now = datetime.now(tz)
    if not range_str:
        return now, now + timedelta(days=7)

    lowered = range_str.strip().lower()
    today = datetime.combine(now.date(), time.min, tzinfo=tz)

    if "aujourd" in lowered:
        return today, today.replace(hour=23, minute=59, second=59)
    if "demain" in lowered:
        start = today + timedelta(days=1)
        return start, start.replace(hour=23, minute=59, second=59)
    if "semaine" in lowered:
        return now, now + timedelta(days=7)
    if "mois" in lowered:
        return now, now + timedelta(days=30)

    parsed = dateparser.parse(
        range_str,
        languages=["fr"],
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": str(tz),
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if parsed is None:
        return now, now + timedelta(days=7)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    start = datetime.combine(parsed.date(), time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def parse_weather_range(when_str: str | None, tz: ZoneInfo) -> tuple[int, int]:
    """Convertit une expression FR en (offset_début, offset_fin) en jours depuis aujourd'hui.

    Défaut (aucune expression) : (0, 0) = aujourd'hui. Sinon, matches explicites
    pour les expressions courantes, fallback dateparser pour le reste.
    """
    if not when_str:
        return 0, 0

    today = datetime.now(tz).date()
    lowered = when_str.strip().lower()

    if "aujourd" in lowered or "ce jour" in lowered or "maintenant" in lowered:
        return 0, 0
    if "après-demain" in lowered or "apres-demain" in lowered:
        return 2, 2
    if "demain" in lowered:
        return 1, 1
    if "weekend" in lowered or "week-end" in lowered:
        wd = today.weekday()  # 0=lundi .. 6=dimanche
        if wd < 5:
            return 5 - wd, 6 - wd
        if wd == 5:
            return 0, 1
        return 0, 0  # dimanche = fin de weekend déjà là
    if "semaine" in lowered:
        return 0, 6

    parsed = dateparser.parse(
        when_str,
        languages=["fr"],
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": str(tz),
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if parsed is None:
        return 0, 0
    offset = (parsed.date() - today).days
    if offset < 0:
        offset = 0
    if offset > 15:
        offset = 15
    return offset, offset


_FR_TIME_SUBSTITUTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "après-midi" DOIT être traité avant "midi" pour éviter que "midi" soit
    # substitué à l'intérieur du mot composé (après-12:00).
    (re.compile(r"\bce\s+matin\b", re.IGNORECASE), "aujourd'hui"),
    (re.compile(r"\bce\s+soir\b", re.IGNORECASE), "aujourd'hui"),
    (re.compile(r"\bcet?\s+après-midi\b", re.IGNORECASE), "aujourd'hui"),
    (re.compile(r"\baprès-midi\b", re.IGNORECASE), ""),
    # "midi" / "minuit" après avoir éliminé "après-midi"
    (re.compile(r"\bmidi\b", re.IGNORECASE), "12:00"),
    (re.compile(r"\bminuit\b", re.IGNORECASE), "00:00"),
    # Mots de moment isolés (ex: "demain matin", "lundi soir") : supprimés car
    # l'heure explicite suffit à dateparser.
    (re.compile(r"\bmatin\b", re.IGNORECASE), ""),
    (re.compile(r"\bsoir\b", re.IGNORECASE), ""),
)

# Heures FR en notation "Xh" / "XhYY" (ex: "7h", "7h30", "19h00"). dateparser
# FR interprète "7h" comme une durée (+7 heures) au lieu de l'heure 07:00,
# donc on normalise en "HH:MM" avant de l'invoquer. Exclu derrière "dans" /
# "il y a" / "depuis" / "pendant" où la notation reste une vraie durée.
_FR_HOUR_PATTERN: re.Pattern[str] = re.compile(r"\b(\d{1,2})h(\d{2})?\b", re.IGNORECASE)
_FR_DURATION_PREFIX: re.Pattern[str] = re.compile(
    r"\b(?:dans|il\s+y\s+a|depuis|pendant)\s+$", re.IGNORECASE
)


def _normalize_fr_hour_markers(expr: str) -> str:
    def replace(match: re.Match[str]) -> str:
        hour = int(match.group(1))
        if not 0 <= hour <= 23:
            return match.group(0)
        if _FR_DURATION_PREFIX.search(expr[: match.start()]):
            return match.group(0)
        minute = match.group(2) or "00"
        return f"{hour:02d}:{minute}"

    return _FR_HOUR_PATTERN.sub(replace, expr)


def normalize_fr_time_words(expr: str) -> str:
    """Remplace les mots FR que dateparser ignore par des expressions qu'il gère."""
    expr = _normalize_fr_hour_markers(expr)
    for pattern, repl in _FR_TIME_SUBSTITUTIONS:
        expr = pattern.sub(repl, expr)
    return " ".join(expr.split())  # nettoie les espaces doubles laissés par les suppressions
