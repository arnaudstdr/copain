// Card prochain évènement (tuile compacte). Iso-visuel de eventCard().
import { Calendar } from "lucide-react";
import type { NextEventCard as EventData } from "../../api/types";
import { formatHM, formatRelativeDay, isAllDayEvent, sameDay } from "../../lib/format";
import { Card, CardHead } from "./Card";

interface Props {
  event: EventData | null;
  onOpen: () => void;
}

export function NextEventCard({ event, onOpen }: Props) {
  if (!event) {
    return (
      <Card compact empty tappable onClick={onOpen}>
        <CardHead icon={Calendar} label="Prochain évènement" />
        <div className="card-primary">Rien à venir</div>
      </Card>
    );
  }

  const start = new Date(event.start);
  const end = new Date(event.end);
  const allDay = isAllDayEvent(start, end);
  const dayWord = sameDay(start, new Date()) ? "Aujourd'hui" : formatRelativeDay(start);
  const dayLabel = allDay ? dayWord : `${dayWord} ${formatHM(start)}`;

  return (
    <Card compact tappable onClick={onOpen}>
      <CardHead icon={Calendar} label={`Prochain évènement · ${event.calendar_name}`} />
      <div className="card-primary">{`${dayLabel} — ${event.title}`}</div>
      {event.location && <div className="card-secondary">{event.location}</div>}
    </Card>
  );
}
