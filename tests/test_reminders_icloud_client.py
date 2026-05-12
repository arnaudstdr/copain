"""Tests du `ICloudRemindersClient` (lib caldav mockée)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from bot.reminders_icloud.client import (
    ICloudRemindersClient,
    ICloudRemindersError,
    _build_vtodo,
    _uid_for_task,
)


@pytest.fixture
def client() -> ICloudRemindersClient:
    return ICloudRemindersClient(
        username="test@icloud.com",
        app_password="xxxx-xxxx",
        list_name="Copain",
        timezone="Europe/Paris",
    )


# --- helpers iCal ---------------------------------------------------------


def test_uid_for_task_is_stable() -> None:
    assert _uid_for_task(42) == "task-42@copain"
    assert _uid_for_task(1) == "task-1@copain"


def test_build_vtodo_minimal() -> None:
    """VTODO sans due_at : pas de ligne DUE, STATUS=NEEDS-ACTION."""
    ical = _build_vtodo("task-1@copain", "Acheter pain", None)
    assert "BEGIN:VTODO" in ical
    assert "END:VTODO" in ical
    assert "UID:task-1@copain" in ical
    assert "SUMMARY:Acheter pain" in ical
    assert "STATUS:NEEDS-ACTION" in ical
    assert "DUE:" not in ical
    # Pas de VALARM volontaire : pas de notif iOS native.
    assert "BEGIN:VALARM" not in ical


def test_build_vtodo_with_due() -> None:
    due = datetime(2026, 5, 15, 18, 0, tzinfo=UTC)
    ical = _build_vtodo("task-2@copain", "Sortir poubelles", due)
    assert "DUE:20260515T180000Z" in ical


def test_build_vtodo_escapes_special_chars() -> None:
    """Les virgules, points-virgules et backslashes sont échappés (RFC 5545)."""
    ical = _build_vtodo("task-3@copain", "Acheter pain, beurre; lait\\maison", None)
    assert "Acheter pain\\, beurre\\; lait\\\\maison" in ical


# --- connect --------------------------------------------------------------


def _vtodo_cal(name: str) -> MagicMock:
    """MagicMock d'une collection CalDAV qui supporte les VTODO."""
    c = MagicMock()
    c.name = name
    c.get_supported_components.return_value = ["VTODO"]
    return c


def _vevent_cal(name: str) -> MagicMock:
    """MagicMock d'une collection CalDAV qui n'accepte QUE les VEVENT.

    Reproduit le piège iCloud : un calendrier nommé 'Rappels ⚠️' qui
    ressemble à une liste Rappels mais qui est en fait un calendrier
    VEVENT classique (`save_todo` retournerait 412 Precondition Failed).
    """
    c = MagicMock()
    c.name = name
    c.get_supported_components.return_value = ["VEVENT"]
    return c


async def test_connect_resolves_existing_vtodo_list(
    client: ICloudRemindersClient,
) -> None:
    fake_list = _vtodo_cal("Copain")
    principal = MagicMock()
    principal.calendars.return_value = [fake_list]
    dav_client = MagicMock()
    dav_client.principal.return_value = principal

    with patch("bot.reminders_icloud.client.caldav.DAVClient", return_value=dav_client):
        await client.connect()

    assert client.is_connected


async def test_connect_ignores_vevent_collections_with_matching_name(
    client: ICloudRemindersClient,
) -> None:
    """Une collection VEVENT du même nom ne doit pas être retenue (412 sinon)."""
    vevent_trap = _vevent_cal("Copain")  # piège : nom matche, mais refuse VTODO
    real_list = _vtodo_cal("Copain Bot")
    principal = MagicMock()
    principal.calendars.return_value = [vevent_trap, real_list]
    dav_client = MagicMock()
    dav_client.principal.return_value = principal

    # Pointe sur "Copain Bot" : doit trouver la vraie liste VTODO.
    client._list_name = "Copain Bot"  # type: ignore[attr-defined]
    with patch("bot.reminders_icloud.client.caldav.DAVClient", return_value=dav_client):
        await client.connect()

    assert client.is_connected


async def test_connect_raises_if_no_vtodo_collection(
    client: ICloudRemindersClient,
) -> None:
    """Si l'utilisateur n'a aucune vraie liste Rappels iCloud → erreur claire."""
    only_vevent = _vevent_cal("Famille")
    principal = MagicMock()
    principal.calendars.return_value = [only_vevent]
    dav_client = MagicMock()
    dav_client.principal.return_value = principal

    with (
        patch("bot.reminders_icloud.client.caldav.DAVClient", return_value=dav_client),
        pytest.raises(ICloudRemindersError, match="Aucune collection CalDAV"),
    ):
        await client.connect()


async def test_connect_raises_if_target_list_not_in_vtodo_collections(
    client: ICloudRemindersClient,
) -> None:
    """Une liste VTODO existe mais pas avec le nom demandé → erreur explicite."""
    other_list = _vtodo_cal("Autre liste")
    principal = MagicMock()
    principal.calendars.return_value = [other_list]
    dav_client = MagicMock()
    dav_client.principal.return_value = principal

    with (
        patch("bot.reminders_icloud.client.caldav.DAVClient", return_value=dav_client),
        pytest.raises(ICloudRemindersError, match="introuvable"),
    ):
        await client.connect()


async def test_connect_wraps_auth_error(client: ICloudRemindersClient) -> None:
    dav_client = MagicMock()
    dav_client.principal.side_effect = RuntimeError("unauthorized")

    with (
        patch("bot.reminders_icloud.client.caldav.DAVClient", return_value=dav_client),
        pytest.raises(ICloudRemindersError, match="Connexion iCloud"),
    ):
        await client.connect()


async def test_connect_is_idempotent(client: ICloudRemindersClient) -> None:
    fake_list = _vtodo_cal("Copain")
    principal = MagicMock()
    principal.calendars.return_value = [fake_list]
    dav_client = MagicMock()
    dav_client.principal.return_value = principal

    with patch("bot.reminders_icloud.client.caldav.DAVClient", return_value=dav_client) as mock_dav:
        await client.connect()
        await client.connect()
    assert mock_dav.call_count == 1


# --- push_todo ------------------------------------------------------------


async def test_push_todo_calls_save_todo(client: ICloudRemindersClient) -> None:
    fake_list = MagicMock()
    fake_list.name = "Copain"
    client._list = fake_list  # type: ignore[attr-defined]

    due = datetime(2026, 5, 15, 18, 0, tzinfo=UTC)
    await client.push_todo(task_id=42, title="Acheter pain", due_at=due)

    fake_list.save_todo.assert_called_once()
    ical_arg = fake_list.save_todo.call_args.args[0]
    assert "UID:task-42@copain" in ical_arg
    assert "SUMMARY:Acheter pain" in ical_arg
    assert "DUE:20260515T180000Z" in ical_arg


async def test_push_todo_raises_if_not_connected(client: ICloudRemindersClient) -> None:
    with pytest.raises(ICloudRemindersError, match="non connecté"):
        await client.push_todo(task_id=1, title="X", due_at=None)


async def test_push_todo_wraps_caldav_error(client: ICloudRemindersClient) -> None:
    fake_list = MagicMock()
    fake_list.save_todo.side_effect = RuntimeError("server down")
    client._list = fake_list  # type: ignore[attr-defined]

    with pytest.raises(ICloudRemindersError, match="Push VTODO échoué"):
        await client.push_todo(task_id=1, title="X", due_at=None)


# --- list_completed_uids -------------------------------------------------


def _fake_todo(ical_data: str) -> MagicMock:
    """Crée un MagicMock de Todo caldav avec un payload `.data` iCalendar."""
    t = MagicMock()
    t.data = ical_data
    return t


async def test_list_completed_uids_filters_completed(client: ICloudRemindersClient) -> None:
    fake_list = MagicMock()
    fake_list.todos.return_value = [
        _fake_todo(
            _build_vtodo("task-1@copain", "Done", None).replace(
                "STATUS:NEEDS-ACTION", "STATUS:COMPLETED"
            )
        ),
        _fake_todo(_build_vtodo("task-2@copain", "Pending", None)),
        _fake_todo(
            _build_vtodo("task-3@copain", "Done aussi", None).replace(
                "STATUS:NEEDS-ACTION", "STATUS:COMPLETED"
            )
        ),
    ]
    client._list = fake_list  # type: ignore[attr-defined]

    uids = await client.list_completed_uids()
    assert sorted(uids) == [1, 3]


async def test_list_completed_uids_ignores_non_copain_uids(
    client: ICloudRemindersClient,
) -> None:
    """Les VTODO créés manuellement (UID non `task-*@copain`) sont ignorés."""
    fake_list = MagicMock()
    fake_list.todos.return_value = [
        _fake_todo(
            _build_vtodo("manual-uuid-123", "Manual task", None).replace(
                "STATUS:NEEDS-ACTION", "STATUS:COMPLETED"
            )
        ),
        _fake_todo(
            _build_vtodo("task-5@copain", "From bot", None).replace(
                "STATUS:NEEDS-ACTION", "STATUS:COMPLETED"
            )
        ),
    ]
    client._list = fake_list  # type: ignore[attr-defined]

    uids = await client.list_completed_uids()
    assert uids == [5]


async def test_list_completed_uids_empty(client: ICloudRemindersClient) -> None:
    fake_list = MagicMock()
    fake_list.todos.return_value = []
    client._list = fake_list  # type: ignore[attr-defined]

    assert await client.list_completed_uids() == []


# --- complete_todo + delete_todo -----------------------------------------


async def test_complete_todo_marks_as_completed(client: ICloudRemindersClient) -> None:
    fake_list = MagicMock()
    target = MagicMock()
    target.data = _build_vtodo("task-7@copain", "À cocher", None)
    fake_list.todos.return_value = [target]
    client._list = fake_list  # type: ignore[attr-defined]

    await client.complete_todo(task_id=7)

    target.complete.assert_called_once()
    target.save.assert_called_once()


async def test_complete_todo_silently_skips_if_not_found(
    client: ICloudRemindersClient,
) -> None:
    fake_list = MagicMock()
    fake_list.todos.return_value = []  # plus aucun VTODO côté iCloud
    client._list = fake_list  # type: ignore[attr-defined]

    # Pas d'exception : on log un warning et on continue.
    await client.complete_todo(task_id=999)


async def test_delete_todo_calls_delete(client: ICloudRemindersClient) -> None:
    fake_list = MagicMock()
    target = MagicMock()
    target.data = _build_vtodo("task-9@copain", "À supprimer", None)
    fake_list.todos.return_value = [target]
    client._list = fake_list  # type: ignore[attr-defined]

    await client.delete_todo(task_id=9)
    target.delete.assert_called_once()
