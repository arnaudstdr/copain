// ── Onglet Accueil (listes groupées iOS) ───────────────────────────────────
// Remplace la grille de cards 2 colonnes par des rows pleine largeur, sans
// troncature (météo, prochain évent, budget, teaser « Pour toi », actu), chacune
// menant à son overlay ou son onglet. La barre de saisie est TEXTE SEUL (step
// 06) : l'envoi bascule sur l'onglet Chat où le message est streamé (plus de
// bulle éphémère). Les données (useDashboard/useNews) sont portées par `App` et
// arrivent en props.

import type { ReactNode } from "react";
import {
  Bell,
  Calendar,
  ChevronRight,
  CloudSun,
  Inbox,
  Newspaper,
  Wallet,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { DashboardResponse } from "../api/types";
import type { TabName } from "../components/TabBar";
import type { NewsState } from "../hooks/useNews";
import {
  formatEur,
  formatHM,
  formatRelativeAge,
  formatRelativeDay,
  greetingDate,
  isAllDayEvent,
  sameDay,
} from "../lib/format";
import { Composer } from "../components/Composer";
import type { Attachment } from "../components/Composer";

const PROFILE_NAME = "Arnaud";

// Overlays de consultation ouvrables depuis l'Accueil. Budget / « Pour toi » /
// dépôts sont des onglets : leurs rows naviguent (onNavigate), sans overlay.
type AccueilOverlay = "weather" | "events" | "notifications";

// Teinte de la pastille d'icône d'une row (couleurs pleines de la maquette).
type RowTint = "teal" | "orange" | "green" | "indigo" | "gray";

interface RowProps {
  icon: LucideIcon;
  tint: RowTint;
  title: string;
  sub?: string | null;
  // Valeur ou pastille à droite (montant, « N enveloppe(s) dépassée(s) »…).
  trailing?: ReactNode;
  onClick?: () => void;
}

// Cellule d'une liste groupée. Un `onClick` la rend tappable (bouton + chevron) ;
// sinon c'est une ligne inerte (ex. météo indisponible).
function Row({ icon: Icon, tint, title, sub, trailing, onClick }: RowProps) {
  const body = (
    <>
      <span className={`row-icon ${tint}`}>
        <Icon size={17} />
      </span>
      <span className="row-body">
        <span className="row-title">{title}</span>
        {sub && <span className="row-sub">{sub}</span>}
      </span>
      {trailing && <span className="row-trailing">{trailing}</span>}
      {onClick && <ChevronRight size={18} className="row-chevron" />}
    </>
  );
  if (onClick) {
    return (
      <button type="button" className="group-row" onClick={onClick}>
        {body}
      </button>
    );
  }
  return <div className="group-row">{body}</div>;
}

interface Props {
  data: DashboardResponse | null;
  error: boolean;
  news: NewsState;
  onOpenNews: () => void;
  unread: number;
  // Envoi de la barre : texte seul (l'attachment est toujours null côté Accueil,
  // la photo n'est proposée qu'au Chat). Bascule sur le Chat, géré par `App`.
  onSend: (text: string, attachment: Attachment | null) => void;
  onOpenOverlay: (name: AccueilOverlay) => void;
  onNavigate: (tab: TabName) => void;
}

// ── Rows dérivées des données du dashboard ──────────────────────────────────

function WeatherRow({ data, onOpen }: { data: DashboardResponse; onOpen: () => void }) {
  const w = data.weather;
  if (!w) {
    return <Row icon={CloudSun} tint="teal" title="Météo indisponible" />;
  }
  const detail =
    `min ${Math.round(w.temp_min)}° / max ${Math.round(w.temp_max)}°` +
    (w.precipitation_mm > 0 ? ` · ${w.precipitation_mm.toFixed(1)} mm` : "");
  return (
    <Row
      icon={CloudSun}
      tint="teal"
      title={`${Math.round(w.temp_current)}° ${w.description}`}
      sub={`${w.city} · ${detail}`}
      onClick={onOpen}
    />
  );
}

function EventRow({ data, onOpen }: { data: DashboardResponse; onOpen: () => void }) {
  const e = data.next_event;
  if (!e) {
    return <Row icon={Calendar} tint="orange" title="Rien à venir" sub="Agenda" onClick={onOpen} />;
  }
  const start = new Date(e.start);
  const end = new Date(e.end);
  const dayWord = sameDay(start, new Date()) ? "Aujourd'hui" : formatRelativeDay(start);
  const when = isAllDayEvent(start, end) ? dayWord : `${dayWord} ${formatHM(start)}`;
  const sub = e.location ? `${when} · ${e.location}` : when;
  return <Row icon={Calendar} tint="orange" title={e.title} sub={sub} onClick={onOpen} />;
}

function BudgetRow({ data, onOpen }: { data: DashboardResponse; onOpen: () => void }) {
  const b = data.budget;
  if (!b) {
    return (
      <Row
        icon={Wallet}
        tint="green"
        title="Budget non configuré"
        sub="Ajoute la section finances au profil"
        onClick={onOpen}
      />
    );
  }
  const overruns = b.envelopes.filter((env) => env.is_overrun).length;
  // Convention projet : un restant négatif s'affiche en AMBRE (jamais rouge),
  // qu'il y ait dépassement d'enveloppe ou non (dépense hors enveloppe,
  // récurrente non pointée…). On réutilise --orange, comme `.row-pastille`.
  const negative = b.remaining_eur < 0;
  const pastille =
    overruns > 0 ? (
      <span className="row-pastille">
        {overruns === 1 ? "1 enveloppe dépassée" : `${overruns} enveloppes dépassées`}
      </span>
    ) : (
      <span
        className="row-value"
        style={negative ? { color: "rgb(var(--orange))" } : undefined}
      >
        {formatEur(b.remaining_eur)}
      </span>
    );
  return (
    <Row
      icon={Wallet}
      tint="green"
      title="Budget"
      sub={overruns > 0 ? `Restant : ${formatEur(b.remaining_eur)}` : "Restant prévisionnel"}
      trailing={pastille}
      onClick={onOpen}
    />
  );
}

function NewsRow({ news, onOpen }: { news: NewsState; onOpen: () => void }) {
  let title: string;
  let sub: string;
  if (news.loading) {
    title = "Chargement…";
    sub = "Curation des dernières 24h";
  } else if (news.markdown) {
    title = "Relire le digest du jour";
    const ago = news.fetchedAt ? formatRelativeAge(news.fetchedAt) : "";
    sub = ago ? `Mis à jour ${ago}` : "Actu du jour";
  } else {
    title = "Les dernières actus";
    sub = "Curation IA des 24h";
  }
  return <Row icon={Newspaper} tint="gray" title={title} sub={sub} onClick={onOpen} />;
}

export function AccueilScreen({
  data,
  error,
  news,
  onOpenNews,
  unread,
  onSend,
  onOpenOverlay,
  onNavigate,
}: Props) {
  return (
    <div className="screen">
      <header>
        <div className="greeting">
          <div className="greeting-name">Bonjour {PROFILE_NAME}</div>
          <div className="greeting-date">{greetingDate()}</div>
        </div>
        <button
          className="header-btn"
          title="Notifications"
          type="button"
          onClick={() => onOpenOverlay("notifications")}
        >
          <Bell size={18} />
          {unread > 0 && <span className="badge">{unread > 9 ? "9+" : unread}</span>}
        </button>
      </header>

      <div className="screen-scroll">
        {data ? (
          <div className="home-body">
            {/* Données déjà chargées : on les garde même si un refresh vient
                d'échouer (blip Tailscale sur le tick 2 min). Le fallback plein
                écran est réservé au cas où l'on n'a jamais rien pu charger. Un
                rafraîchissement périmé se signale par une note discrète. */}
            {error && <div className="group-label">Données du dernier chargement</div>}
            <div className="group-label">Aujourd'hui</div>
            <div className="group">
              <WeatherRow data={data} onOpen={() => onOpenOverlay("weather")} />
              <EventRow data={data} onOpen={() => onOpenOverlay("events")} />
            </div>

            <div className="group-label">Suivi</div>
            <div className="group">
              <BudgetRow data={data} onOpen={() => onNavigate("budget")} />
              <Row
                icon={Inbox}
                tint="indigo"
                title="Pour toi"
                sub="Tes dépôts, remis en lumière au bon moment"
                onClick={() => onNavigate("pensees")}
              />
              <NewsRow news={news} onOpen={onOpenNews} />
            </div>
          </div>
        ) : error ? (
          <div className="home-body">
            <div className="card empty">
              <div className="card-primary">Dashboard indisponible</div>
              <div className="card-secondary">
                Vérifie que le Pi est accessible via Tailscale. La prochaine
                actualisation automatique réessaiera.
              </div>
            </div>
          </div>
        ) : null}
      </div>

      {/* Barre texte seul : l'envoi bascule sur le Chat (jamais async ici, donc
          jamais busy) ; la réponse s'affiche dans le fil du Chat. */}
      <Composer variant="dashboard" busy={false} onSend={onSend} />
    </div>
  );
}
