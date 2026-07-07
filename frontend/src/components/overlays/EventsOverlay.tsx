// ── Overlay Évènements ───────────────────────────────────────────────────────
// GET /events?days=7 → agenda iCloud à venir, regroupé par jour civil LOCAL.
// toISOString() est en UTC : un all-day Paris remonterait à la veille, donc on
// lit les composantes locales pour la clé de groupe (comme renderEvents() de
// bot/static/js/overlays.js). Iso-visuel.

import { useEffect, useState } from "react";
import { Calendar, MapPin } from "lucide-react";
import { apiGet } from "../../api/client";
import type { EventsListResponse, CalendarEventItem } from "../../api/types";
import { formatHM, formatRelativeDay, isAllDayEvent } from "../../lib/format";
import { ActionButtons } from "../ActionButtons";
import { Overlay, PanelEmpty } from "../Overlay";

interface Props {
  onClose: () => void;
}

type Status = "loading" | "error" | "ready";

// Clé de groupe = jour civil local (YYYY-MM-DD).
function localDayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function EventsOverlay({ onClose }: Props) {
  const [events, setEvents] = useState<CalendarEventItem[]>([]);
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    let cancelled = false;
    apiGet<EventsListResponse>("/events?days=7")
      .then((data) => {
        if (cancelled) return;
        setEvents(data.events ?? []);
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Regroupement par jour, en préservant l'ordre d'arrivée (déjà trié backend).
  const groups: { key: string; events: CalendarEventItem[] }[] = [];
  for (const e of events) {
    const key = localDayKey(new Date(e.start));
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.events.push(e);
    else groups.push({ key, events: [e] });
  }

  return (
    <Overlay icon={Calendar} title="Évènements à venir" onClose={onClose}>
      <div className="panel-list">
        {status === "loading" ? (
          <PanelEmpty>Chargement…</PanelEmpty>
        ) : status === "error" ? (
          <PanelEmpty>Impossible de charger</PanelEmpty>
        ) : events.length === 0 ? (
          <PanelEmpty>Aucun évènement à venir</PanelEmpty>
        ) : (
          groups.map((g) => (
            <div className="events-day-group" key={g.key}>
              <div className="events-day-label">
                {formatRelativeDay(new Date(`${g.key}T00:00:00`))}
              </div>
              {g.events.map((e) => (
                <EventItem key={e.uid} event={e} />
              ))}
            </div>
          ))
        )}
      </div>
    </Overlay>
  );
}

function EventItem({ event }: { event: CalendarEventItem }) {
  const start = new Date(event.start);
  const end = new Date(event.end);
  const timeLabel = isAllDayEvent(start, end)
    ? "Toute la journée"
    : `${formatHM(start)} – ${formatHM(end)}`;
  return (
    <div className="event-item">
      <div className="event-time">{timeLabel}</div>
      <div className="event-title">{event.title}</div>
      {event.location && (
        <div className="event-location">
          <MapPin size={12} />
          {event.location}
        </div>
      )}
      <div className="event-calendar">{event.calendar_name}</div>
      <ActionButtons actions={event.actions} variant="subtle" />
    </div>
  );
}
