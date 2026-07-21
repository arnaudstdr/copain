// ── Coquille de navigation : 4 onglets + tab bar iOS ────────────────────────
// L'onglet actif est un `useState<TabName>` (décision 1 SPEC, ouverture toujours
// sur Accueil, pas de routeur). Seul l'écran actif est monté (décision 2) ;
// l'état du chat vit hors React (`chatStore`) et les données dashboard restent
// ici (useDashboard/useNews), passées en props à l'Accueil. Les overlays de
// consultation/interaction (météo, agenda, notifs, dépôt, pour toi, budget,
// actu) sont rendus au niveau `App`, ouverts par `setOpenOverlay`.

import { useCallback, useEffect, useState } from "react";
import { Check } from "lucide-react";
import { useDashboard } from "./hooks/useDashboard";
import { useNews } from "./hooks/useNews";
import { useToast } from "./components/Toast";
import type { AskResponse, ExpenseDraft } from "./api/types";
import { TabBar } from "./components/TabBar";
import type { TabName } from "./components/TabBar";
import { Fab } from "./components/Fab";
import { FabSheet } from "./components/FabSheet";
import { AccueilScreen } from "./screens/AccueilScreen";
import { BudgetScreen } from "./screens/BudgetScreen";
import { ChatScreen } from "./screens/ChatScreen";
import { PenseesScreen } from "./screens/PenseesScreen";
import { NotificationsOverlay } from "./components/overlays/NotificationsOverlay";
import { TasksOverlay } from "./components/overlays/TasksOverlay";
import { WeatherOverlay } from "./components/overlays/WeatherOverlay";
import { EventsOverlay } from "./components/overlays/EventsOverlay";
import { MarkdownView } from "./components/MarkdownView";
import { invalidateForYou } from "./lib/foryouCache";

// Toast de retour d'action (intent modifiant l'état) — pastille verte + libellé.
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

// Overlays de consultation ouverts depuis les taps de card / header. Budget,
// « Pour toi » et les dépôts sont devenus des onglets (step 03) — ils ne
// passent plus par un overlay.
type OverlayName = "weather" | "events" | "tasks" | "news" | "notifications";

export default function App() {
  const { data, error, refresh } = useDashboard();
  const { news, open: openNews, refresh: refreshNews, refreshing: newsRefreshing } = useNews();
  const toast = useToast();
  const [tab, setTab] = useState<TabName>("accueil");
  const [openOverlay, setOpenOverlay] = useState<OverlayName | null>(null);
  // FAB global : feuille d'actions ouverte + compteur bumpé à chaque saisie
  // réussie (dépôt/dépense). Le compteur force le refetch de l'écran monté
  // (Budget/Pensées) puisque le FAB vit hors des écrans.
  const [fabOpen, setFabOpen] = useState(false);
  const [fabTick, setFabTick] = useState(0);
  // Message saisi depuis la barre de l'Accueil, en attente de streaming par le
  // Chat au montage (bascule Accueil → Chat). Brouillon de dépense lu d'une
  // capture photo (ouvre l'onglet Budget pré-rempli).
  const [pendingChat, setPendingChat] = useState<string | null>(null);
  const [budgetDraft, setBudgetDraft] = useState<ExpenseDraft | null>(null);
  // unread affiché : forcé à 0 dès que l'overlay notifs a lu (le GET purge côté
  // backend), en attendant le prochain refresh du dashboard.
  const [notifsRead, setNotifsRead] = useState(false);

  // Le masque local du badge non-lu (posé à la lecture des notifs, cf. notifsRead)
  // est levé dès qu'un refresh rapporte l'état serveur à jour : de nouvelles
  // notifs réapparaissent alors normalement.
  useEffect(() => {
    setNotifsRead(false);
  }, [data]);

  const close = () => setOpenOverlay(null);
  // Fermeture d'un overlay qui a pu bouger l'état du dashboard (cochage de
  // tâche, lecture de notifs) → on re-synchronise les cards.
  const closeAndRefresh = () => {
    close();
    void refresh();
  };

  // Navigation d'onglet manuelle (tab bar ou card Accueil) : purge toujours le
  // brouillon de dépense. Un brouillon n'existe que le temps d'une bascule
  // automatique vers Budget après lecture d'une photo (cf. handleAskResponse) ;
  // tout tap explicite repart donc d'un formulaire vierge.
  const goTab = useCallback((next: TabName) => {
    setBudgetDraft(null);
    setTab(next);
  }, []);

  // Frame `done` du stream chat : rafraîchit les cards impactées. « foryou »
  // invalide le cache de restitution (refetch au prochain tap, canal pull) ;
  // le reste passe par le refresh du dashboard.
  const handleRefreshCards = useCallback(
    (cards: string[]) => {
      if (cards.includes("foryou")) invalidateForYou();
      void refresh();
    },
    [refresh],
  );

  // Barre de l'Accueil (texte seul) : on met le message en attente puis on
  // bascule sur le Chat, qui le streame à son montage (un seul historique, plus
  // de bulle éphémère). setTab direct (pas goTab) : goTab purgerait un éventuel
  // budgetDraft, sans intérêt ici mais on garde la bascule minimale.
  const handleDashboardSend = useCallback((text: string) => {
    setPendingChat(text);
    setTab("chat");
  }, []);

  // Effets de bord d'une réponse /ask/image émise depuis le Chat (reprend la
  // logique de l'ex-bulle éphémère) : brouillon de dépense → bascule Budget
  // pré-rempli (renvoie true = navigation, le Chat ne pousse pas de bulle) ;
  // sinon resync des cards impactées, avec toast SAUF si la réponse porte des
  // actions (leurs boutons dans la bulle rendent le toast redondant). La réponse
  // texte est rendue en bulle par le Chat.
  const handlePhotoSideEffects = useCallback(
    (body: AskResponse): boolean => {
      if (body.expense_draft) {
        // setTab direct : on CONSERVE le brouillon jusqu'au montage de BudgetScreen.
        setBudgetDraft(body.expense_draft);
        setTab("budget");
        return true;
      }
      if (body.refresh_cards.length > 0) {
        if (!body.actions?.length) toast(actionToast(body.intent));
        handleRefreshCards(body.refresh_cards);
      }
      return false;
    },
    [toast, handleRefreshCards],
  );

  // Saisie FAB réussie : ferme la feuille, resync le dashboard et bumpe le
  // compteur pour rafraîchir la liste visible de l'écran monté (Budget/Pensées).
  const handleFabSubmitted = useCallback(() => {
    setFabOpen(false);
    setFabTick((t) => t + 1);
    void refresh();
  }, [refresh]);

  // Callbacks inline mémoïsés (passés aux écrans/overlays) : identités stables
  // pour ne pas déstabiliser les hooks des écrans (notamment ChatScreen).
  const handleOpenNews = useCallback(
    () => void openNews(() => setOpenOverlay("news")),
    [openNews],
  );
  const handleOpenOverlay = useCallback((name: OverlayName) => setOpenOverlay(name), []);
  const handlePendingConsumed = useCallback(() => setPendingChat(null), []);
  const openFab = useCallback(() => setFabOpen(true), []);

  // CR-012 : le FAB ne doit jamais pouvoir s'empiler par-dessus (ou glisser sous)
  // un overlay ouvert ou en cours d'ouverture. On le masque tant qu'un overlay
  // est monté OU qu'un fetch d'actu est en vol (openNews → setOpenOverlay async).
  const fabBlocked = openOverlay !== null || news.loading;

  const unread = notifsRead ? 0 : (data?.unread_notifications ?? 0);

  return (
    <div id="app" data-tab={tab}>
      {tab === "accueil" && (
        <AccueilScreen
          data={data}
          error={Boolean(error)}
          news={news}
          onOpenNews={handleOpenNews}
          unread={unread}
          onSend={handleDashboardSend}
          onOpenOverlay={handleOpenOverlay}
          onNavigate={goTab}
        />
      )}
      {tab === "budget" && (
        <BudgetScreen draft={budgetDraft} onChanged={refresh} reloadKey={fabTick} />
      )}
      {tab === "chat" && (
        <ChatScreen
          onRefreshCards={handleRefreshCards}
          pending={pendingChat}
          onPendingConsumed={handlePendingConsumed}
          onPhotoSideEffects={handlePhotoSideEffects}
        />
      )}
      {tab === "pensees" && <PenseesScreen onChanged={refresh} reloadKey={fabTick} />}

      <TabBar active={tab} onSelect={goTab} />
      {!fabBlocked && <Fab onClick={openFab} />}
      {fabOpen && <FabSheet onClose={() => setFabOpen(false)} onSubmitted={handleFabSubmitted} />}

      {/* ── Overlays de consultation ── */}
      {openOverlay === "notifications" && (
        <NotificationsOverlay onClose={closeAndRefresh} onRead={() => setNotifsRead(true)} />
      )}
      {openOverlay === "tasks" && <TasksOverlay onClose={closeAndRefresh} />}
      {openOverlay === "weather" && <WeatherOverlay onClose={close} />}
      {openOverlay === "events" && <EventsOverlay onClose={close} />}
      {openOverlay === "news" && news.markdown && (
        <MarkdownView
          title="Actu du jour"
          subtitle={news.fetchedAt ?? undefined}
          markdown={news.markdown}
          onClose={close}
          action={{ label: newsRefreshing ? "…" : "Actualiser", onClick: refreshNews }}
        />
      )}
    </div>
  );
}
