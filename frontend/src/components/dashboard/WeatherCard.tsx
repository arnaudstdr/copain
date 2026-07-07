// Card météo (tuile compacte). Iso-visuel de weatherCard() de dashboard.js.
import { CloudSun } from "lucide-react";
import type { WeatherCard as WeatherData } from "../../api/types";
import { Card, CardHead } from "./Card";

interface Props {
  weather: WeatherData | null;
  onOpen: () => void;
}

export function WeatherCard({ weather, onOpen }: Props) {
  if (!weather) {
    return (
      <Card compact empty>
        <CardHead icon={CloudSun} label="Météo" />
        <div className="card-primary">Indisponible</div>
      </Card>
    );
  }

  const tempLine = `${Math.round(weather.temp_current)}° · ${weather.description}`;
  const detail =
    `min ${Math.round(weather.temp_min)}° / max ${Math.round(weather.temp_max)}°` +
    (weather.precipitation_mm > 0 ? ` · ${weather.precipitation_mm.toFixed(1)} mm` : "");

  return (
    <Card compact tappable onClick={onOpen}>
      <CardHead icon={CloudSun} label={`Météo · ${weather.city}`} />
      <div className="card-primary">{tempLine}</div>
      <div className="card-secondary">{detail}</div>
    </Card>
  );
}
