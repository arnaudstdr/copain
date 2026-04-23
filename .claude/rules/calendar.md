---
paths:
  - "bot/calendar/**"
---

# iCloud calendar (CalDAV)

`ICloudCalendarClient` connects to `https://caldav.icloud.com/` via the
`caldav` library (synchronous, wrapped in `asyncio.to_thread`). Auth via an
App-Specific Password.

On `connect()`, all available calendars are listed and stored in
`self._all_calendars`. The `resolve_calendar(name)` method performs a
3-level tolerant match:

1. Exact match
2. Normalised match (NFC + strip ZWJ `‍` + variation selectors `️` + trim +
   casefold)
3. "Contains" match on the alphanumeric-only version

Consequence: the user can write `ICLOUD_CALENDAR_NAME=Personnel` or ask "in
the sport calendar" even if the real names are `🧘‍♂️ Personnel` and
`🚴‍♂️ Sport` with emojis + spaces.

Current scope: **create + list**. No delete/modification/recurrence — do not
add without discussing the scope first.

When editing, keep in mind that `post_init` in `bot/main.py` tolerates a
connection failure: a missing/invalid Apple ID password must log a warning
and let the bot start. The `event` intent then returns a user-facing
"calendar unavailable" message.
