// ── Écran unique : dashboard + shell (header, composer, bord bas iOS 26) ─────
// Le dashboard (GET /dashboard) est chargé au boot puis rafraîchi (120 s). Les
// overlays de consultation (step 05 : notifs, tâches, météo, évents, actu) sont
// pilotés par `openOverlay`. Les overlays interactifs (dépôt/pour toi/budget,
// step 06) et le chat (07) complètent l'UI. Le composer (step 08) envoie sur
// /ask (bulle éphémère + toast) ou /ask/image (photo).

import { useEffect, useRef, useState } from "react";
import { Bell, Check, MessageSquare } from "lucide-react";
import { useDashboard } from "./hooks/useDashboard";
import { useNews } from "./hooks/useNews";
import { useToast } from "./components/Toast";
import { askImage, askText } from "./api/client";
import type { Action, ExpenseDraft } from "./api/types";
import { greetingDate } from "./lib/format";
import { Composer } from "./components/Composer";
import type { Attachment } from "./components/Composer";
import { Ephemeral } from "./components/Ephemeral";
import type { EphemeralData } from "./components/Ephemeral";
import { BudgetCard } from "./components/dashboard/BudgetCard";
import { DepotExpressCard } from "./components/dashboard/DepotExpressCard";
import { ForYouCard } from "./components/dashboard/ForYouCard";
import { NewsCard } from "./components/dashboard/NewsCard";
import { NextEventCard } from "./components/dashboard/NextEventCard";
import { WeatherCard } from "./components/dashboard/WeatherCard";
import { NotificationsOverlay } from "./components/overlays/NotificationsOverlay";
import { TasksOverlay } from "./components/overlays/TasksOverlay";
import { WeatherOverlay } from "./components/overlays/WeatherOverlay";
import { EventsOverlay } from "./components/overlays/EventsOverlay";
import { DepotExpressOverlay } from "./components/overlays/DepotExpressOverlay";
import { ForYouOverlay } from "./components/overlays/ForYouOverlay";
import { BudgetOverlay } from "./components/overlays/BudgetOverlay";
import { MarkdownView } from "./components/MarkdownView";
import { ChatView } from "./components/chat/ChatView";
import { invalidateForYou } from "./lib/foryouCache";

const PROFILE_NAME = "Arnaud";

// Toast de retour d'action (intent modifiant l'état) — pastille verte + libellé.
// Portage de actionToast() de bot/static/js/composer.js.
function actionToast(intent: string) {
  const labels: Record<string, string> = {
    task: "Tâche ajoutée",
    event: "Évènement créé",
    feed: "Flux mis à jour",
    memory: "Noté en mémoire",
    expense: "Saisie enregistrée",
  };
  return (
    <span className="toast-content">
      <Check size={14} />
      {labels[intent] ?? "Fait"}
    </span>
  );
}

// Overlays et vues plein écran ouvertes depuis les taps de card / header.
// L'entrée chat est posée mais rendue au step 07 ; son tap ne rend rien encore.
type OverlayName =
  | "weather"
  | "events"
  | "tasks"
  | "depot"
  | "foryou"
  | "budget"
  | "news"
  | "notifications"
  | "chat";

export default function App() {
  const { data, error, refresh } = useDashboard();
  const { news, open: openNews, refresh: refreshNews, refreshing: newsRefreshing } = useNews();
  const toast = useToast();
  const [openOverlay, setOpenOverlay] = useState<OverlayName | null>(null);
  // Envoi /ask en cours (désactive le composer), bulle éphémère affichée, et
  // brouillon de dépense lu d'une capture (ouvre le Budget pré-rempli).
  const [asking, setAsking] = useState(false);
  const [ephemeral, setEphemeral] = useState<EphemeralData | null>(null);
  const [budgetDraft, setBudgetDraft] = useState<ExpenseDraft | null>(null);
  // unread affiché : forcé à 0 dès que l'overlay notifs a lu (le GET purge côté
  // backend), en attendant le prochain refresh du dashboard.
  const [notifsRead, setNotifsRead] = useState(false);
  // Révélation échelonnée des cards au 1er rendu seulement (cf. .revealed dans
  // index.css) : on fige après la 1re animation pour ne pas la rejouer aux
  // refresh périodiques.
  const [revealed, setRevealed] = useState(false);
  const revealedOnce = useRef(false);

  useEffect(() => {
    if (data && !revealedOnce.current) {
      revealedOnce.current = true;
      const id = setTimeout(() => setRevealed(true), 600);
      return () => clearTimeout(id);
    }
    return undefined;
  }, [data]);

  // Le masque local du badge non-lu (posé à la lecture des notifs, cf. notifsRead)
  // est levé dès qu'un refresh rapporte l'état serveur à jour : de nouvelles
  // notifs réapparaissent alors normalement. Parité vanilla, où le prochain
  // loadDashboard réécrasait la valeur locale forcée à 0.
  useEffect(() => {
    setNotifsRead(false);
  }, [data]);

  const close = () => setOpenOverlay(null);
  // Fermeture d'un overlay qui a pu bouger l'état du dashboard (cochage de
  // tâche, lecture de notifs) → on re-synchronise les cards (comme le vanilla).
  const closeAndRefresh = () => {
    close();
    void refresh();
  };

  // Frame `done` du stream chat : rafraîchit les cards impactées. « foryou »
  // invalide le cache de restitution (refetch au prochain tap, canal pull) ;
  // le reste passe par le refresh du dashboard (portage de invalidateCards +
  // loadDashboard du vanilla).
  const handleRefreshCards = (cards: string[]) => {
    if (cards.includes("foryou")) invalidateForYou();
    void refresh();
  };

  // Traitement de la réponse /ask (portage de handleAskResponse du vanilla) :
  // brouillon de dépense → Budget pré-rempli ; action proposée → bulle
  // persistante avec bouton (+ resync silencieux des cards) ; action seule
  // (refresh_cards) → toast + resync ; sinon → réponse texte en bulle éphémère.
  const handleAskResponse = (body: {
    response: string;
    intent: string;
    refresh_cards: string[];
    actions?: Action[];
    expense_draft: ExpenseDraft | null;
  }) => {
    if (body.expense_draft) {
      setBudgetDraft(body.expense_draft);
      setOpenOverlay("budget");
      return;
    }
    const actions = body.actions ?? [];
    if (actions.length > 0) {
      // La bulle porte le(s) bouton(s) et reste jusqu'au tap : on resync les
      // cards en silence (pas de toast redondant avec la bulle affichée).
      if (body.refresh_cards.length > 0) handleRefreshCards(body.refresh_cards);
      setEphemeral({ text: body.response, isError: false, actions });
      return;
    }
    if (body.refresh_cards.length > 0) {
      toast(actionToast(body.intent));
      handleRefreshCards(body.refresh_cards);
    } else {
      setEphemeral({ text: body.response, isError: false });
    }
  };

  // Composer du dashboard : /ask (texte) ou /ask/image (photo), non streamé.
  const handleDashboardSend = (text: string, attachment: Attachment | null) => {
    setAsking(true);
    const call = attachment
      ? askImage(text, attachment.b64, attachment.mediaType)
      : askText(text);
    call
      .then(handleAskResponse)
      .catch(() => setEphemeral({ text: "Impossible de joindre Copain. Vérifie Tailscale.", isError: true }))
      .finally(() => setAsking(false));
  };

  const unread = notifsRead ? 0 : (data?.unread_notifications ?? 0);

  return (
    <>
      <div id="app" data-overlay={openOverlay ?? undefined}>
        <header>
          <div className="greeting">
            <div className="greeting-name">Bonjour {PROFILE_NAME}</div>
            <div className="greeting-date">{greetingDate()}</div>
          </div>
          <button
            className="header-btn"
            title="Ouvrir la conversation"
            type="button"
            onClick={() => setOpenOverlay("chat")}
          >
            <MessageSquare size={18} />
          </button>
          <button
            className="header-btn"
            title="Notifications"
            type="button"
            onClick={() => setOpenOverlay("notifications")}
          >
            <Bell size={18} />
            {unread > 0 && <span className="badge">{unread > 9 ? "9+" : unread}</span>}
          </button>
        </header>

        <div id="dashboard" className={revealed ? "revealed" : undefined}>
          {error ? (
            <div className="card empty">
              <div className="card-primary">Dashboard indisponible</div>
              <div className="card-secondary">
                Vérifie que le Pi est accessible via Tailscale, puis tire pour rafraîchir.
              </div>
            </div>
          ) : data ? (
            <>
              <div className="card-grid">
                <WeatherCard weather={data.weather} onOpen={() => setOpenOverlay("weather")} />
                <NextEventCard event={data.next_event} onOpen={() => setOpenOverlay("events")} />
              </div>
              <div className="card-grid">
                <DepotExpressCard onOpen={() => setOpenOverlay("depot")} />
                <ForYouCard onOpen={() => setOpenOverlay("foryou")} />
              </div>
              <BudgetCard
                budget={data.budget}
                onOpen={() => {
                  setBudgetDraft(null);
                  setOpenOverlay("budget");
                }}
              />
              <NewsCard news={news} onOpen={() => void openNews(() => setOpenOverlay("news"))} />
            </>
          ) : null}
        </div>

        {/* Bulle éphémère (réponse /ask, non persistée) + composer. */}
        {ephemeral && <Ephemeral data={ephemeral} onHide={() => setEphemeral(null)} />}
        <Composer variant="dashboard" busy={asking} onSend={handleDashboardSend} />

        {/* ── Overlays de consultation (step 05) ── */}
        {openOverlay === "notifications" && (
          <NotificationsOverlay onClose={closeAndRefresh} onRead={() => setNotifsRead(true)} />
        )}
        {openOverlay === "tasks" && <TasksOverlay onClose={closeAndRefresh} />}
        {openOverlay === "weather" && <WeatherOverlay onClose={close} />}
        {openOverlay === "events" && <EventsOverlay onClose={close} />}

        {/* ── Overlays interactifs (step 06) ── */}
        {openOverlay === "depot" && (
          <DepotExpressOverlay onClose={closeAndRefresh} onRefresh={() => void refresh()} />
        )}
        {openOverlay === "foryou" && <ForYouOverlay onClose={closeAndRefresh} />}
        {openOverlay === "budget" && (
          <BudgetOverlay
            draft={budgetDraft}
            onClose={() => {
              setBudgetDraft(null);
              closeAndRefresh();
            }}
          />
        )}
        {openOverlay === "news" && news.markdown && (
          <MarkdownView
            title="Actu du jour"
            subtitle={news.fetchedAt ?? undefined}
            markdown={news.markdown}
            onClose={close}
            action={{ label: newsRefreshing ? "…" : "Actualiser", onClick: refreshNews }}
          />
        )}

        {/* ── Mode dialogue (step 07) ── */}
        {openOverlay === "chat" && <ChatView onClose={close} onRefreshCards={handleRefreshCards} />}
      </div>

      {/* Lame de verre échantillonnée par iOS 26 pour peindre la bande système
          sous le web view (cf. #edge-glass dans index.css). */}
      <div id="edge-glass" aria-hidden="true" />
    </>
  );
}
