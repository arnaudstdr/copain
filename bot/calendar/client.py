"""Client CalDAV iCloud (lib `caldav` sync wrappée via asyncio.to_thread)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import caldav
import vobject

from bot.calendar.models import CalendarEvent
from bot.logging_conf import get_logger

log = get_logger(__name__)

ICLOUD_CALDAV_URL = "https://caldav.icloud.com/"


class ICloudCalendarError(RuntimeError):
    """Erreur réseau, auth ou calendrier introuvable côté iCloud."""


class ICloudCalendarClient:
    """Wrapper async minimal autour de `caldav.DAVClient`.

    La lib caldav est synchrone, donc chaque appel réseau passe par
    `asyncio.to_thread` pour ne pas bloquer l'event loop Telegram.

    La connexion est lazy : appeler `connect()` une fois au démarrage pour
    résoudre le calendrier cible. En cas d'échec, l'erreur est levée à
    l'appelant qui peut choisir de logger sans crash (le bot reste utilisable
    pour les autres intents).
    """

    def __init__(
        self,
        username: str,
        app_password: str,
        calendar_name: str,
        timezone: str = "Europe/Paris",
    ) -> None:
        self._username = username
        self._password = app_password
        self._calendar_name = calendar_name
        self._tz = ZoneInfo(timezone)
        self._calendar: Any | None = None
        self._all_calendars: list[Any] = []

    @property
    def is_connected(self) -> bool:
        return self._calendar is not None

    def list_calendar_names(self) -> list[str]:
        """Retourne la liste des noms de calendriers découverts au connect."""
        return [getattr(c, "name", "?") for c in self._all_calendars]

    async def connect(self) -> None:
        """Résout le calendrier cible. Idempotent."""
        if self._calendar is not None:
            return
        self._calendar = await asyncio.to_thread(self._sync_connect)
        log.info("calendar_connected", calendar=self._calendar_name)

    CALDAV_TIMEOUT_SEC = 15

    def _sync_connect(self) -> Any:
        try:
            # cast(Any, ...) : certains versions de mypy voient caldav.DAVClient
            # comme `object` (via ignore_missing_imports) et refusent l'appel ;
            # le cast évite d'avoir à ajouter un `# type: ignore[operator]` qui
            # deviendrait lui-même "unused" sur les versions plus récentes.
            dav_client_cls = cast(Any, caldav.DAVClient)
            client: Any = dav_client_cls(
                url=ICLOUD_CALDAV_URL,
                username=self._username,
                password=self._password,
                timeout=self.CALDAV_TIMEOUT_SEC,
            )
            principal = client.principal()
            calendars = list(principal.calendars())
        except Exception as exc:
            raise ICloudCalendarError(f"Connexion iCloud échouée : {exc}") from exc

        self._all_calendars = calendars
        log.info(
            "calendars_discovered",
            count=len(calendars),
            names=[getattr(c, "name", "?") for c in calendars],
        )

        match = _find_calendar(calendars, self._calendar_name)
        if match is not None:
            matched_name = getattr(match, "name", "?")
            if matched_name != self._calendar_name:
                log.info(
                    "calendar_fuzzy_match",
                    requested=self._calendar_name,
                    matched=matched_name,
                )
            return match

        available = [getattr(c, "name", "?") for c in calendars]
        raise ICloudCalendarError(
            f"Calendrier '{self._calendar_name}' introuvable. Disponibles : {available}"
        )

    def resolve_calendar(self, name: str | None) -> Any:
        """Retourne le calendrier matchant `name` (fuzzy), ou le calendrier défaut.

        Raise ICloudCalendarError si `name` ne matche aucun calendrier disponible.
        """
        if name is None or not name.strip():
            return self._require_connected()
        match = _find_calendar(self._all_calendars, name)
        if match is None:
            available = [getattr(c, "name", "?") for c in self._all_calendars]
            raise ICloudCalendarError(f"Calendrier '{name}' introuvable. Disponibles : {available}")
        return match

    async def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        location: str | None = None,
        description: str | None = None,
        calendar_name: str | None = None,
    ) -> CalendarEvent:
        cal = self.resolve_calendar(calendar_name)
        target_name = getattr(cal, "name", self._calendar_name)
        start_aware = _ensure_aware(start, self._tz)
        end_aware = _ensure_aware(end, self._tz)
        event_uid = f"{uuid.uuid4().hex}@copain"

        ical = _build_vevent(
            uid=event_uid,
            title=title,
            start=start_aware,
            end=end_aware,
            location=location,
            description=description,
        )
        await asyncio.to_thread(cal.save_event, ical)
        log.info(
            "calendar_event_created",
            title=title,
            start=start_aware.isoformat(),
            end=end_aware.isoformat(),
            calendar=target_name,
        )
        return CalendarEvent(
            uid=event_uid,
            title=title,
            start=start_aware,
            end=end_aware,
            location=location,
            description=description,
            calendar_name=target_name,
        )

    async def list_between(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        cal = self._require_connected()
        start_aware = _ensure_aware(start, self._tz)
        end_aware = _ensure_aware(end, self._tz)
        raw = await asyncio.to_thread(
            cal.date_search,
            start_aware,
            end_aware,
            expand=True,
        )
        events: list[CalendarEvent] = []
        for entry in raw:
            parsed = _parse_vevent(entry, self._calendar_name, self._tz)
            if parsed is not None:
                events.append(parsed)
        events.sort(key=lambda e: e.start)
        return events

    async def list_today(self) -> list[CalendarEvent]:
        now = datetime.now(self._tz)
        start = datetime.combine(now.date(), time.min, tzinfo=self._tz)
        end = datetime.combine(now.date(), time.max, tzinfo=self._tz)
        return await self.list_between(start, end)

    async def list_upcoming(self, days: int = 7) -> list[CalendarEvent]:
        now = datetime.now(self._tz)
        end = now + timedelta(days=days)
        return await self.list_between(now, end)

    async def list_all_between(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """Agrège les évènements de TOUS les calendriers iCloud découverts.

        Itère sur `self._all_calendars` ; une exception sur un calendrier donné
        (perm révoquées, CalDAV flaky...) est loggée en warning et n'interrompt
        pas les autres — un calendrier cassé ne doit pas tuer le briefing.

        Chaque `CalendarEvent` porte le nom du calendrier d'origine dans
        `calendar_name`, pas le nom du calendrier par défaut.
        """
        if not self._all_calendars:
            raise ICloudCalendarError("Client iCloud non connecté. Appelle connect() d'abord.")
        start_aware = _ensure_aware(start, self._tz)
        end_aware = _ensure_aware(end, self._tz)
        events: list[CalendarEvent] = []
        for cal in self._all_calendars:
            cal_name = getattr(cal, "name", "?") or "?"
            try:
                raw = await asyncio.to_thread(
                    cal.date_search,
                    start_aware,
                    end_aware,
                    expand=True,
                )
            except Exception as exc:
                log.warning("calendar_list_failed_for", calendar=cal_name, error=str(exc))
                continue
            for entry in raw:
                parsed = _parse_vevent(entry, cal_name, self._tz)
                if parsed is not None:
                    events.append(parsed)
        events.sort(key=lambda e: e.start)
        return events

    async def list_all_today(self) -> list[CalendarEvent]:
        now = datetime.now(self._tz)
        start = datetime.combine(now.date(), time.min, tzinfo=self._tz)
        end = datetime.combine(now.date(), time.max, tzinfo=self._tz)
        return await self.list_all_between(start, end)

    async def list_all_upcoming(self, days: int = 7) -> list[CalendarEvent]:
        now = datetime.now(self._tz)
        end = now + timedelta(days=days)
        return await self.list_all_between(now, end)

    def _require_connected(self) -> Any:
        if self._calendar is None:
            raise ICloudCalendarError("Client iCloud non connecté. Appelle connect() d'abord.")
        return self._calendar


def _ensure_aware(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def _find_calendar(calendars: list[Any], requested: str) -> Any | None:
    """Matching tolérant aux espaces, à la casse et aux emojis ZWJ/variation-selectors.

    iCloud renvoie souvent des noms avec emojis (ex: '🧘‍♂️ Personnel '). Les copier
    parfaitement depuis le .env est hasardeux (ZWJ, variation selectors, espaces),
    donc on tente plusieurs stratégies de match du plus strict au plus laxiste.
    """
    import unicodedata

    def normalize(s: str) -> str:
        # NFC compose, supprime ZWJ (U+200D) et variation selectors (U+FE0F, U+FE0E)
        nfc = unicodedata.normalize("NFC", s)
        cleaned = nfc.replace("‍", "").replace("️", "").replace("︎", "")
        return cleaned.strip().casefold()

    target = normalize(requested)

    # 1. Match exact (préférence stricte)
    for cal in calendars:
        if getattr(cal, "name", None) == requested:
            return cal

    # 2. Match normalisé (trim + case-fold + ZWJ/VS)
    for cal in calendars:
        if normalize(getattr(cal, "name", "") or "") == target:
            return cal

    # 3. Match "contient" sur la version alphanumérique seulement
    alnum_target = "".join(c for c in target if c.isalnum())
    if alnum_target:
        for cal in calendars:
            name = getattr(cal, "name", "") or ""
            alnum = "".join(c for c in normalize(name) if c.isalnum())
            if alnum_target in alnum:
                return cal

    return None


def _build_vevent(
    uid: str,
    title: str,
    start: datetime,
    end: datetime,
    location: str | None,
    description: str | None,
) -> str:
    """Sérialise un VEVENT iCalendar minimal.

    On génère manuellement (plutôt que via vobject) pour garder un contrôle total
    sur le format UTC des dates et éviter les gymnastics tzinfo. Format DTSTART
    conforme RFC 5545 : `YYYYMMDDTHHMMSSZ` après conversion en UTC.
    """

    def _ical_utc(dt: datetime) -> str:
        utc_dt = dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
        return utc_dt.strftime("%Y%m%dT%H%M%SZ")

    def _escape(value: str) -> str:
        return (
            value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
        )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//copain//iCloud Calendar//FR",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{_escape(title)}",
        f"DTSTART:{_ical_utc(start)}",
        f"DTEND:{_ical_utc(end)}",
        f"DTSTAMP:{_ical_utc(datetime.now(UTC))}",
    ]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(lines) + "\r\n"


def _parse_vevent(entry: Any, calendar_name: str, tz: ZoneInfo) -> CalendarEvent | None:
    try:
        ical = vobject.readOne(entry.data)
    except Exception as exc:
        log.warning("calendar_event_parse_failed", error=str(exc))
        return None

    vevent = getattr(ical, "vevent", None)
    if vevent is None:
        return None

    try:
        uid = str(vevent.uid.value)
        title = str(getattr(vevent, "summary", _Empty()).value) or "(sans titre)"
        start = _to_aware(vevent.dtstart.value, tz)
        if hasattr(vevent, "dtend"):
            end = _to_aware(vevent.dtend.value, tz)
        else:
            end = start + timedelta(hours=1)
    except AttributeError as exc:
        log.warning("calendar_event_missing_field", error=str(exc))
        return None

    location = str(vevent.location.value) if hasattr(vevent, "location") else None
    description = str(vevent.description.value) if hasattr(vevent, "description") else None

    return CalendarEvent(
        uid=uid,
        title=title,
        start=start,
        end=end,
        location=location,
        description=description,
        calendar_name=calendar_name,
    )


def _to_aware(value: Any, tz: ZoneInfo) -> datetime:
    """Accepte datetime ou date, retourne datetime timezone-aware dans `tz`.

    iCloud sérialise souvent les VEVENT en UTC (`DTSTART:...Z`). vobject
    renvoie alors un datetime aware en UTC, qui affichait l'heure UTC au
    lieu de l'heure locale (décalage CEST = -2h visible côté briefing).
    On convertit donc systématiquement vers la timezone locale demandée.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value.astimezone(tz)
    return datetime.combine(value, time.min, tzinfo=tz)


class _Empty:
    value = ""
