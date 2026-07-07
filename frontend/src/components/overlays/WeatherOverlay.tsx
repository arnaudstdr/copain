// ── Overlay Météo ────────────────────────────────────────────────────────────
// GET /weather/forecast?days=7&hours=24 → strip horaire (24h glissantes) +
// lignes quotidiennes (7 jours). Lieu contextualisé côté backend (position
// courante). Iso-visuel de openWeather()/renderWeather() de overlays.js.

import { useEffect, useState } from "react";
import { CloudSun } from "lucide-react";
import { apiGet } from "../../api/client";
import type {
  WeatherForecastResponse,
  HourlyForecastItem,
  DailyForecastItem,
} from "../../api/types";
import { formatHM, formatRelativeDay } from "../../lib/format";
import { weatherIcon } from "../../lib/weatherIcon";
import { Overlay, PanelEmpty } from "../Overlay";

interface Props {
  onClose: () => void;
}

type Status = "loading" | "error" | "ready";

export function WeatherOverlay({ onClose }: Props) {
  const [data, setData] = useState<WeatherForecastResponse | null>(null);
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    let cancelled = false;
    apiGet<WeatherForecastResponse>("/weather/forecast?days=7&hours=24")
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const title = data ? `Météo · ${data.city}` : "Météo";

  return (
    <Overlay icon={CloudSun} title={title} onClose={onClose}>
      <div className="panel-list">
        {status === "loading" ? (
          <PanelEmpty>Chargement…</PanelEmpty>
        ) : status === "error" || !data ? (
          <PanelEmpty>Impossible de charger</PanelEmpty>
        ) : (
          <>
            {data.hourly.length > 0 && (
              <>
                <div className="weather-section-title">Heure par heure</div>
                <div className="weather-hourly-strip">
                  {data.hourly.map((h, i) => (
                    <HourCell key={i} hour={h} />
                  ))}
                </div>
              </>
            )}
            {data.daily.length > 0 && (
              <>
                <div className="weather-section-title">Prochains jours</div>
                {data.daily.map((d, i) => (
                  <DayRow key={i} day={d} />
                ))}
              </>
            )}
          </>
        )}
      </div>
    </Overlay>
  );
}

function HourCell({ hour }: { hour: HourlyForecastItem }) {
  const Icon = weatherIcon(hour.description);
  const showPrecip = hour.precipitation_probability_pct >= 30 || hour.precipitation_mm > 0.1;
  const precip =
    hour.precipitation_mm > 0.1
      ? `${hour.precipitation_mm.toFixed(1)} mm`
      : `${hour.precipitation_probability_pct}%`;
  return (
    <div className="weather-hour">
      <div className="weather-hour-time">{formatHM(new Date(hour.time))}</div>
      <div className="weather-hour-icon">
        <Icon />
      </div>
      <div className="weather-hour-temp">{Math.round(hour.temp_c)}°</div>
      {showPrecip && <div className="weather-hour-precip">{precip}</div>}
    </div>
  );
}

function DayRow({ day }: { day: DailyForecastItem }) {
  const Icon = weatherIcon(day.description);
  return (
    <div className="weather-day">
      <div className="weather-day-label">{formatRelativeDay(new Date(day.date))}</div>
      <div className="weather-day-icon">
        <Icon />
      </div>
      <div className="weather-day-desc">{day.description}</div>
      <div className="weather-day-temps">
        {Math.round(day.temp_min)}° / {Math.round(day.temp_max)}°
      </div>
      {day.precipitation_mm > 0.1 && (
        <div className="weather-day-precip">{day.precipitation_mm.toFixed(1)} mm</div>
      )}
    </div>
  );
}
