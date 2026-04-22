"""Tests du client iCloud CalDAV (caldav mocké)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from bot.calendar.client import ICloudCalendarClient, ICloudCalendarError


@pytest.fixture
def client() -> ICloudCalendarClient:
    return ICloudCalendarClient(
        username="test@icloud.com",
        app_password="xxxx-xxxx",
        calendar_name="Personnel",
        timezone="Europe/Paris",
    )


async def test_connect_resolves_calendar_by_name(client: ICloudCalendarClient) -> None:
    fake_cal = MagicMock(name="Personnel")
    fake_cal.name = "Personnel"
    other_cal = MagicMock(name="Famille")
    other_cal.name = "Famille"
    principal = MagicMock()
    principal.calendars.return_value = [other_cal, fake_cal]
    dav_client = MagicMock()
    dav_client.principal.return_value = principal

    with patch("bot.calendar.client.caldav.DAVClient", return_value=dav_client):
        await client.connect()

    assert client.is_connected
    assert client._calendar is fake_cal  # type: ignore[attr-defined]


async def test_connect_raises_if_calendar_not_found(client: ICloudCalendarClient) -> None:
    other = MagicMock()
    other.name = "Famille"
    principal = MagicMock()
    principal.calendars.return_value = [other]
    dav_client = MagicMock()
    dav_client.principal.return_value = principal

    with (
        patch("bot.calendar.client.caldav.DAVClient", return_value=dav_client),
        pytest.raises(ICloudCalendarError, match="introuvable"),
    ):
        await client.connect()


async def test_connect_wraps_auth_error(client: ICloudCalendarClient) -> None:
    dav_client = MagicMock()
    dav_client.principal.side_effect = RuntimeError("unauthorized")

    with (
        patch("bot.calendar.client.caldav.DAVClient", return_value=dav_client),
        pytest.raises(ICloudCalendarError, match="Connexion iCloud"),
    ):
        await client.connect()


async def test_connect_is_idempotent(client: ICloudCalendarClient) -> None:
    fake_cal = MagicMock()
    fake_cal.name = "Personnel"
    principal = MagicMock()
    principal.calendars.return_value = [fake_cal]
    dav_client = MagicMock()
    dav_client.principal.return_value = principal

    with patch("bot.calendar.client.caldav.DAVClient", return_value=dav_client) as mock_dav:
        await client.connect()
        await client.connect()
    assert mock_dav.call_count == 1


async def test_create_event_calls_save_event(client: ICloudCalendarClient) -> None:
    fake_cal = MagicMock()
    fake_cal.name = "Personnel"
    fake_cal.save_event = MagicMock()
    client._calendar = fake_cal  # type: ignore[attr-defined]

    start = datetime(2026, 4, 22, 15, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    event = await client.create_event(
        title="RDV dentiste", start=start, end=end, location="Sélestat"
    )

    assert event.title == "RDV dentiste"
    assert event.location == "Sélestat"
    assert fake_cal.save_event.called
    ical_sent = fake_cal.save_event.call_args[0][0]
    assert "SUMMARY:RDV dentiste" in ical_sent
    assert "LOCATION:Sélestat" in ical_sent


async def test_create_event_requires_connection(client: ICloudCalendarClient) -> None:
    start = datetime(2026, 4, 22, 15, 0, tzinfo=UTC)
    with pytest.raises(ICloudCalendarError, match="non connecté"):
        await client.create_event(title="Test", start=start, end=start + timedelta(hours=1))


async def test_create_event_routes_to_named_calendar(
    client: ICloudCalendarClient,
) -> None:
    """Quand `calendar_name` est fourni, on utilise ce calendrier (fuzzy match)."""
    default_cal = MagicMock()
    default_cal.name = "Personnel"
    default_cal.save_event = MagicMock()
    sport_cal = MagicMock()
    sport_cal.name = "🚴 Sport "
    sport_cal.save_event = MagicMock()
    client._calendar = default_cal  # type: ignore[attr-defined]
    client._all_calendars = [default_cal, sport_cal]  # type: ignore[attr-defined]

    start = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    event = await client.create_event(
        title="Vélo",
        start=start,
        end=start + timedelta(hours=2),
        calendar_name="sport",
    )
    assert sport_cal.save_event.called
    assert not default_cal.save_event.called
    assert event.calendar_name == "🚴 Sport "


async def test_create_event_unknown_calendar_raises(
    client: ICloudCalendarClient,
) -> None:
    default_cal = MagicMock()
    default_cal.name = "Personnel"
    client._calendar = default_cal  # type: ignore[attr-defined]
    client._all_calendars = [default_cal]  # type: ignore[attr-defined]

    start = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    with pytest.raises(ICloudCalendarError, match="introuvable"):
        await client.create_event(
            title="Test",
            start=start,
            end=start + timedelta(hours=1),
            calendar_name="inexistant",
        )


async def test_connect_matches_emoji_calendar_by_trimmed_name() -> None:
    """Si l'utilisateur tape 'Personnel' mais le vrai nom est '🧘‍♂️ Personnel ',
    le matching tolérant (trim + ZWJ + variation selectors) doit fonctionner.
    """
    client = ICloudCalendarClient(
        username="test@icloud.com",
        app_password="xxxx",
        calendar_name="Personnel",
        timezone="Europe/Paris",
    )
    emoji_cal = MagicMock()
    emoji_cal.name = "🧘‍♂️ Personnel "
    other = MagicMock()
    other.name = "Pro"
    principal = MagicMock()
    principal.calendars.return_value = [other, emoji_cal]
    dav_client = MagicMock()
    dav_client.principal.return_value = principal

    with patch("bot.calendar.client.caldav.DAVClient", return_value=dav_client):
        await client.connect()
    assert client._calendar is emoji_cal  # type: ignore[attr-defined]


async def test_list_today_uses_local_date(client: ICloudCalendarClient) -> None:
    fake_cal = MagicMock()
    fake_cal.name = "Personnel"
    fake_cal.date_search = MagicMock(return_value=[])
    client._calendar = fake_cal  # type: ignore[attr-defined]

    events = await client.list_today()
    assert events == []
    assert fake_cal.date_search.called
    start_arg, end_arg = fake_cal.date_search.call_args[0][:2]
    # Même date, couvrant toute la journée.
    assert start_arg.date() == end_arg.date()
    assert start_arg.hour == 0
    assert end_arg.hour == 23


async def test_list_all_between_aggregates_calendars(client: ICloudCalendarClient) -> None:
    """list_all_between agrège tous les calendriers et tag chaque event avec son calendrier d'origine."""
    ical_perso = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:perso-1@copain
SUMMARY:RDV médecin
DTSTART:20260422T140000Z
DTEND:20260422T150000Z
END:VEVENT
END:VCALENDAR"""
    ical_sport = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:sport-1@copain
SUMMARY:Vélo
DTSTART:20260422T100000Z
DTEND:20260422T120000Z
END:VEVENT
END:VCALENDAR"""
    entry_perso = MagicMock()
    entry_perso.data = ical_perso
    entry_sport = MagicMock()
    entry_sport.data = ical_sport

    cal_perso = MagicMock()
    cal_perso.name = "Personnel"
    cal_perso.date_search = MagicMock(return_value=[entry_perso])
    cal_sport = MagicMock()
    cal_sport.name = "🚴 Sport"
    cal_sport.date_search = MagicMock(return_value=[entry_sport])

    client._all_calendars = [cal_perso, cal_sport]  # type: ignore[attr-defined]

    start = datetime(2026, 4, 22, 0, 0, tzinfo=UTC)
    end = datetime(2026, 4, 22, 23, 59, tzinfo=UTC)
    events = await client.list_all_between(start, end)

    # Ordre trié par start : Vélo (10h) avant RDV médecin (14h).
    assert [e.title for e in events] == ["Vélo", "RDV médecin"]
    assert events[0].calendar_name == "🚴 Sport"
    assert events[1].calendar_name == "Personnel"


async def test_list_all_between_tolerates_per_calendar_failure(
    client: ICloudCalendarClient,
) -> None:
    """Une panne sur un calendrier ne doit pas masquer les events des autres."""
    ical = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:ok-1@copain
SUMMARY:Tour en ville
DTSTART:20260422T090000Z
DTEND:20260422T100000Z
END:VEVENT
END:VCALENDAR"""
    entry = MagicMock()
    entry.data = ical

    cal_ok1 = MagicMock()
    cal_ok1.name = "Personnel"
    cal_ok1.date_search = MagicMock(return_value=[entry])
    cal_broken = MagicMock()
    cal_broken.name = "Partagé"
    cal_broken.date_search = MagicMock(side_effect=RuntimeError("403 forbidden"))
    cal_ok2 = MagicMock()
    cal_ok2.name = "Sport"
    cal_ok2.date_search = MagicMock(return_value=[])

    client._all_calendars = [cal_ok1, cal_broken, cal_ok2]  # type: ignore[attr-defined]

    start = datetime(2026, 4, 22, 0, 0, tzinfo=UTC)
    end = datetime(2026, 4, 22, 23, 59, tzinfo=UTC)
    events = await client.list_all_between(start, end)

    assert [e.title for e in events] == ["Tour en ville"]
    assert cal_broken.date_search.called  # on a bien tenté


async def test_list_all_between_requires_connection(client: ICloudCalendarClient) -> None:
    start = datetime(2026, 4, 22, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    with pytest.raises(ICloudCalendarError, match="non connecté"):
        await client.list_all_between(start, end)


async def test_list_between_parses_and_sorts(client: ICloudCalendarClient) -> None:
    ical_1 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:evt-2@copain
SUMMARY:Second
DTSTART:20260422T150000Z
DTEND:20260422T160000Z
END:VEVENT
END:VCALENDAR"""
    ical_2 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:evt-1@copain
SUMMARY:First
DTSTART:20260422T090000Z
DTEND:20260422T100000Z
END:VEVENT
END:VCALENDAR"""
    entry_1 = MagicMock()
    entry_1.data = ical_1
    entry_2 = MagicMock()
    entry_2.data = ical_2
    fake_cal = MagicMock()
    fake_cal.name = "Personnel"
    fake_cal.date_search = MagicMock(return_value=[entry_1, entry_2])
    client._calendar = fake_cal  # type: ignore[attr-defined]

    start = datetime(2026, 4, 22, 0, 0, tzinfo=UTC)
    end = datetime(2026, 4, 22, 23, 59, tzinfo=UTC)
    events = await client.list_between(start, end)
    assert [e.title for e in events] == ["First", "Second"]
