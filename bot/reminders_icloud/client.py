"""Client CalDAV iCloud spécialisé pour les VTODO (Apple Rappels).

Calque l'archi de `bot.calendar.client.ICloudCalendarClient` (lib caldav
synchrone wrappée via asyncio.to_thread, fuzzy match du nom de liste,
lazy connect avec tolérance aux pannes). Distinct du client calendrier
parce qu'il opère sur une liste différente (créée au démarrage si
absente) et que les VTODO ont un format iCalendar différent des VEVENT.

Le format VTODO injecté est volontairement minimal : pas de VALARM. Ça
évite la double notification (Apple Rappels enverrait sa propre notif
iOS au due time alors qu'APScheduler côté backend pousse déjà via
Pushover). On garde Pushover comme canal principal de notif et on
expose Rappels comme vitrine cochable.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import caldav
import vobject

from bot.logging_conf import get_logger
from bot.reminders_icloud.models import ICloudReminder

log = get_logger(__name__)

ICLOUD_CALDAV_URL = "https://caldav.icloud.com/"
UID_PATTERN = re.compile(r"^task-(\d+)@copain$")


class ICloudRemindersError(RuntimeError):
    """Erreur réseau, auth ou liste introuvable côté iCloud Rappels."""


def _uid_for_task(task_id: int) -> str:
    """UID stable pour matcher un VTODO à une task DB."""
    return f"task-{task_id}@copain"


class ICloudRemindersClient:
    """Wrapper async sur la lib `caldav` pour la liste Rappels iCloud."""

    CALDAV_TIMEOUT_SEC = 15

    def __init__(
        self,
        username: str,
        app_password: str,
        list_name: str = "Copain",
        timezone: str = "Europe/Paris",
    ) -> None:
        self._username = username
        self._password = app_password
        self._list_name = list_name
        self._tz = ZoneInfo(timezone)
        self._list: Any | None = None  # caldav Calendar (VTODO-only)

    @property
    def is_connected(self) -> bool:
        return self._list is not None

    async def connect(self) -> None:
        """Trouve ou crée la liste Rappels dédiée. Idempotent."""
        if self._list is not None:
            return
        self._list = await asyncio.to_thread(self._sync_connect)
        log.info("reminders_list_connected", name=self._list_name)

    def _sync_connect(self) -> Any:
        try:
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
            raise ICloudRemindersError(f"Connexion iCloud Rappels échouée : {exc}") from exc

        # Filtrage par supported-calendar-component-set : iCloud expose dans
        # `principal.calendars()` à la fois les calendriers VEVENT et les
        # vraies listes Rappels VTODO. Pour ne pas tenter de pousser un VTODO
        # dans un calendrier qui le refuse (412 Precondition Failed), on
        # garde seulement les collections qui acceptent VTODO. La méthode
        # `get_supported_components()` de caldav fait un PROPFIND CalDAV.
        vtodo_capable: list[Any] = []
        for cal in calendars:
            cal_name = getattr(cal, "name", "?") or "?"
            try:
                components = cal.get_supported_components()
            except Exception as exc:
                log.warning(
                    "reminders_components_check_failed",
                    name=cal_name,
                    error=str(exc),
                )
                continue
            log.info(
                "reminders_calendar_components",
                name=cal_name,
                supports=list(components),
            )
            if "VTODO" in components:
                vtodo_capable.append(cal)

        if not vtodo_capable:
            raise ICloudRemindersError(
                "Aucune collection CalDAV n'accepte les VTODO côté iCloud. "
                "Crée une liste manuellement dans Rappels.app sur iPhone, "
                "puis pointe `ICLOUD_REMINDERS_LIST_NAME` sur son nom."
            )

        match = _find_calendar(vtodo_capable, self._list_name)
        if match is not None:
            matched_name = getattr(match, "name", "?")
            if matched_name != self._list_name:
                log.info(
                    "reminders_list_fuzzy_match",
                    requested=self._list_name,
                    matched=matched_name,
                )
            return match

        available = [getattr(c, "name", "?") for c in vtodo_capable]
        raise ICloudRemindersError(
            f"Liste Rappels '{self._list_name}' introuvable parmi les collections "
            f"VTODO disponibles : {available}. Vérifie `ICLOUD_REMINDERS_LIST_NAME`."
        )

    async def push_todo(
        self,
        task_id: int,
        title: str,
        due_at: datetime | None,
    ) -> None:
        """Crée ou met à jour le VTODO pour cette task. Idempotent (UID stable)."""
        cal = self._require_connected()
        ical = _build_vtodo(
            uid=_uid_for_task(task_id),
            title=title,
            due_at=due_at,
        )
        try:
            await asyncio.to_thread(cal.save_todo, ical)
        except Exception as exc:
            raise ICloudRemindersError(f"Push VTODO échoué (task {task_id}) : {exc}") from exc
        log.info("reminder_pushed", task_id=task_id, due_at=due_at.isoformat() if due_at else None)

    async def complete_todo(self, task_id: int) -> None:
        """Marque le VTODO comme COMPLETED. No-op si introuvable."""
        cal = self._require_connected()
        uid = _uid_for_task(task_id)
        todo = await asyncio.to_thread(self._find_todo_by_uid, cal, uid)
        if todo is None:
            log.warning("reminder_complete_not_found", task_id=task_id)
            return

        def _mark_completed(t: Any) -> None:
            t.complete()
            t.save()

        try:
            await asyncio.to_thread(_mark_completed, todo)
        except Exception as exc:
            raise ICloudRemindersError(f"Complete VTODO échoué (task {task_id}) : {exc}") from exc
        log.info("reminder_completed", task_id=task_id)

    async def delete_todo(self, task_id: int) -> None:
        """Supprime le VTODO. No-op si introuvable."""
        cal = self._require_connected()
        uid = _uid_for_task(task_id)
        todo = await asyncio.to_thread(self._find_todo_by_uid, cal, uid)
        if todo is None:
            return
        try:
            await asyncio.to_thread(todo.delete)
        except Exception as exc:
            raise ICloudRemindersError(f"Delete VTODO échoué (task {task_id}) : {exc}") from exc
        log.info("reminder_deleted", task_id=task_id)

    async def list_completed_uids(self) -> list[int]:
        """Retourne les task_id dont le VTODO est complété côté iOS.

        Filtre les UID qui matchent le pattern `task-{N}@copain` et garde
        seulement ceux dont STATUS=COMPLETED. Les autres VTODO (créés
        manuellement côté iPhone, par exemple) sont ignorés.
        """
        cal = self._require_connected()
        try:
            todos = await asyncio.to_thread(cal.todos, include_completed=True)
        except Exception as exc:
            raise ICloudRemindersError(f"Listing VTODO échoué : {exc}") from exc

        completed_ids: list[int] = []
        for t in todos:
            parsed = _parse_vtodo(t)
            if parsed is None or not parsed.completed:
                continue
            match = UID_PATTERN.match(parsed.uid)
            if match is not None:
                completed_ids.append(int(match.group(1)))
        return completed_ids

    def _require_connected(self) -> Any:
        if self._list is None:
            raise ICloudRemindersError("Client Rappels non connecté. Appelle connect() d'abord.")
        return self._list

    def _find_todo_by_uid(self, cal: Any, uid: str) -> Any | None:
        """Itère sur tous les VTODO (completed+pending) pour trouver le bon UID."""
        try:
            todos = cal.todos(include_completed=True)
        except Exception:
            return None
        for t in todos:
            parsed = _parse_vtodo(t)
            if parsed is not None and parsed.uid == uid:
                return t
        return None


def _build_vtodo(uid: str, title: str, due_at: datetime | None) -> str:
    """Construit un VTODO iCalendar conforme aux exigences iCloud Reminders.

    iCloud rejette avec `412 Precondition Failed` les VTODO trop minimalistes :
    il exige au moins `CREATED`, `LAST-MODIFIED` et `DTSTAMP` (en plus de
    `UID` et `SUMMARY`). On ajoute aussi `SEQUENCE:0` car certaines versions
    de l'app Rappels iOS s'en servent pour la résolution de conflits de sync.

    Format RFC 5545. Pas de DTSTART (on parle de tâche, pas d'évent).
    DUE en UTC (avec suffixe Z). STATUS NEEDS-ACTION par défaut. Pas de
    VALARM volontaire (la notif vient d'APScheduler/Pushover, pas d'iOS).
    """

    def _ical_utc(dt: datetime) -> str:
        utc_dt = dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
        return utc_dt.strftime("%Y%m%dT%H%M%SZ")

    def _escape(value: str) -> str:
        return (
            value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
        )

    now_utc = _ical_utc(datetime.now(UTC))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//copain//iCloud Reminders//FR",
        "BEGIN:VTODO",
        f"UID:{uid}",
        f"SUMMARY:{_escape(title)}",
        f"DTSTAMP:{now_utc}",
        f"CREATED:{now_utc}",
        f"LAST-MODIFIED:{now_utc}",
        "SEQUENCE:0",
        "STATUS:NEEDS-ACTION",
    ]
    if due_at is not None:
        lines.append(f"DUE:{_ical_utc(due_at)}")
    lines.extend(["END:VTODO", "END:VCALENDAR"])
    return "\r\n".join(lines) + "\r\n"


def _parse_vtodo(entry: Any) -> ICloudReminder | None:
    """Lit un Todo caldav et le mappe vers `ICloudReminder`. None si KO."""
    try:
        ical = vobject.readOne(entry.data)
    except Exception as exc:
        log.warning("vtodo_parse_failed", error=str(exc))
        return None

    vtodo = getattr(ical, "vtodo", None)
    if vtodo is None:
        return None

    try:
        uid = str(vtodo.uid.value)
        title = str(getattr(vtodo, "summary", _Empty()).value) or "(sans titre)"
    except AttributeError:
        return None

    due_at: datetime | None = None
    if hasattr(vtodo, "due"):
        raw_due = vtodo.due.value
        if isinstance(raw_due, datetime):
            due_at = raw_due if raw_due.tzinfo is not None else raw_due.replace(tzinfo=UTC)

    status = getattr(vtodo, "status", _Empty()).value if hasattr(vtodo, "status") else ""
    completed = str(status).upper() == "COMPLETED"

    return ICloudReminder(uid=uid, title=title, due_at=due_at, completed=completed)


def _find_calendar(calendars: list[Any], requested: str) -> Any | None:
    """Matching tolérant aux espaces, à la casse et aux emojis ZWJ/VS.

    Réutilise la même logique que `bot.calendar.client._find_calendar`.
    Dupliqué ici pour rester self-contained et éviter d'introduire un
    module utils calé sur 30 lignes communes — si un 3e client CalDAV
    apparaît, on factorisera.
    """
    import unicodedata

    def normalize(s: str) -> str:
        nfc = unicodedata.normalize("NFC", s)
        cleaned = nfc.replace("‍", "").replace("️", "").replace("︎", "")
        return cleaned.strip().casefold()

    target = normalize(requested)

    for cal in calendars:
        if getattr(cal, "name", None) == requested:
            return cal

    for cal in calendars:
        if normalize(getattr(cal, "name", "") or "") == target:
            return cal

    alnum_target = "".join(c for c in target if c.isalnum())
    if alnum_target:
        for cal in calendars:
            name = getattr(cal, "name", "") or ""
            alnum = "".join(c for c in normalize(name) if c.isalnum())
            if alnum_target in alnum:
                return cal

    return None


class _Empty:
    value = ""
