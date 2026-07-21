// ── Onglet Chat (mode dialogue) ─────────────────────────────────────────────
// Ex-`ChatView` plein écran (position:fixed), devenu l'écran de l'onglet Chat :
// il n'est plus un overlay et perd son bouton « retour » (la navigation passe
// par la tab bar). Feed de bulles (historique persisté + échanges de session),
// envoi streamé SSE sur POST /ask/stream, rendu markdown. Scroll infini vers le
// haut avec séparateurs de jour et préservation de la position au prepend.
// L'état du fil vit hors React (`chatStore`) → il survit au démontage lors d'un
// changement d'onglet. Le trombone photo route vers /ask/image (réponse en un
// bloc, bulles poussées dans le fil).

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Bot, Brain } from "lucide-react";
import { useHistory } from "../hooks/useHistory";
import { useChatStream } from "../hooks/useChatStream";
import { askImage } from "../api/client";
import type { AskResponse } from "../api/types";
import { appendMessage } from "../lib/chatStore";
import { formatDaySeparator, sameDay } from "../lib/format";
import { Markdown } from "../components/Markdown";
import { Composer } from "../components/Composer";
import type { Attachment } from "../components/Composer";
import { DaySeparator } from "../components/chat/DaySeparator";
import { MessageBubble } from "../components/chat/MessageBubble";

interface Props {
  onRefreshCards: (cards: string[]) => void;
  // Message saisi depuis la barre de l'Accueil, à streamer au montage du Chat
  // (bascule Accueil → Chat). `null` quand il n'y a rien à consommer.
  pending: string | null;
  onPendingConsumed: () => void;
  // Effets de bord d'une réponse /ask/image (brouillon de dépense, resync, toast) ;
  // renvoie `true` si la réponse déclenche une navigation (pas de bulle à pousser).
  onPhotoSideEffects: (body: AskResponse) => boolean;
}

export function ChatScreen({ onRefreshCards, pending, onPendingConsumed, onPhotoSideEffects }: Props) {
  const { messages, loaded, loadOlder, loadingOlder } = useHistory();
  const { send, streaming, liveText } = useChatStream({ onRefreshCards });
  // Verrou de consommation du message en attente : le Chat est monté à neuf à
  // chaque bascule depuis l'Accueil, on ne streame donc qu'une fois par montage.
  const pendingConsumed = useRef(false);
  // Envoi photo (non streamé) en cours : partage l'indicateur « écrit… » avec le
  // streaming texte et désactive le composer.
  const [imgBusy, setImgBusy] = useState(false);
  // Mode « réflexion » (thinking) : toggle à la volée, session-only (remis à off
  // au reload). Override OLLAMA_THINK pour le prochain message streamé.
  const [think, setThink] = useState(false);
  const feedRef = useRef<HTMLDivElement>(null);
  // Hauteur du feed AVANT un prepend de page ancienne : sert à recaler le scroll
  // après re-rendu pour éviter le saut visuel (null hors prepend).
  const prevHeightRef = useRef<number | null>(null);

  // Après chaque changement du fil : préserve la position au prepend d'anciens
  // messages, colle en bas sinon (hydratation, envoi, deltas live, réponse).
  useLayoutEffect(() => {
    const feed = feedRef.current;
    if (!feed) return;
    if (prevHeightRef.current != null) {
      feed.scrollTop = feed.scrollHeight - prevHeightRef.current;
      prevHeightRef.current = null;
      return;
    }
    feed.scrollTop = feed.scrollHeight;
  }, [messages, liveText, streaming, imgBusy, loaded]);

  const onScroll = useCallback(() => {
    const feed = feedRef.current;
    // On sort si un chargement est déjà en vol : sinon les onScroll répétés d'un
    // même geste écraseraient la hauteur mémorisée (et la remettraient à null via
    // leur `.then`) avant l'arrivée du vrai prepend → saut de scroll. Seul l'appel
    // qui déclenche réellement le fetch touche `prevHeightRef`.
    if (!feed || feed.scrollTop >= 40 || loadingOlder.current) return;
    // Mémorise la hauteur avant le prepend ; annulé si loadOlder n'a rien ajouté.
    prevHeightRef.current = feed.scrollHeight;
    void loadOlder().then((didPrepend) => {
      if (!didPrepend) prevHeightRef.current = null;
    });
  }, [loadOlder, loadingOlder]);

  // Photo : /ask/image (pas de streaming). Bulle utilisateur (avec aperçu) puis
  // réponse ou message d'erreur, poussés dans le fil. Les effets de bord (brouillon
  // de dépense → bascule Budget, resync, toast) sont délégués à `App` via
  // `onPhotoSideEffects` : s'il renvoie `true`, la réponse a déclenché une
  // navigation (ex. ticket → Budget pré-rempli) et on ne pousse pas de bulle.
  // Portage de chatSend (att).
  const sendChatImage = useCallback(
    async (text: string, att: Attachment) => {
      appendMessage({ role: "user", text, imgSrc: att.preview, createdAt: new Date().toISOString() });
      setImgBusy(true);
      try {
        const body = await askImage(text, att.b64, att.mediaType);
        if (onPhotoSideEffects(body)) return;
        appendMessage({
          role: "assistant",
          text: body.response,
          actions: body.actions?.length ? body.actions : undefined,
          createdAt: new Date().toISOString(),
        });
      } catch {
        appendMessage({
          role: "assistant",
          text: "Impossible de joindre Copain.",
          error: true,
          createdAt: new Date().toISOString(),
        });
      } finally {
        setImgBusy(false);
      }
    },
    [onPhotoSideEffects],
  );

  // Bascule Accueil → Chat : streame le message en attente une seule fois au
  // montage (le verrou survit aux re-renders ; `onPendingConsumed` remet le
  // slot d'`App` à null). think=false : la barre de l'Accueil n'a pas le toggle.
  useEffect(() => {
    if (pending == null || pendingConsumed.current) return;
    pendingConsumed.current = true;
    void send(pending);
    onPendingConsumed();
  }, [pending, send, onPendingConsumed]);

  const onSend = useCallback(
    (text: string, attachment: Attachment | null) => {
      if (attachment) void sendChatImage(text, attachment);
      else void send(text, think);
    },
    [sendChatImage, send, think],
  );

  const busy = streaming || imgBusy;
  // Bulle live seulement quand il y a du texte visible ; sinon indicateur
  // d'attente. Se baser sur liveText (et pas un flag « a reçu un delta ») évite
  // le blanc après un `replace("")` (search/recall/handlers) où plus rien ne
  // s'affichait.
  const showLive = streaming && liveText.length > 0;

  // Mémoïsé sur [messages, loaded] : chaque delta SSE ne fait varier que
  // `liveText` (rendu à part), inutile de reparcourir tout l'historique à
  // chaque chunk.
  const feed = useMemo(() => renderFeed(messages, loaded), [messages, loaded]);

  return (
    <div className="screen chat-screen">
      <header>
        <div className="greeting">
          <div className="greeting-name">Conversation</div>
          <div className="greeting-date">Mode dialogue</div>
        </div>
        <button
          className={`header-btn${think ? " thinking-on" : ""}`}
          title={think ? "Mode réflexion activé" : "Activer le mode réflexion"}
          aria-pressed={think}
          type="button"
          onClick={() => setThink((v) => !v)}
        >
          <Brain size={18} />
        </button>
      </header>

      <div className="chat-feed" ref={feedRef} onScroll={onScroll}>
        {feed}
        {busy && !showLive && (
          <div className="row bot typing-row">
            <div className="avatar-sm">
              <Bot size={16} />
            </div>
            <div className="bubble bot typing">
              {think && streaming ? (
                <span className="thinking-label">réflexion en cours…</span>
              ) : (
                <>
                  <span className="dot" />
                  <span className="dot" />
                  <span className="dot" />
                </>
              )}
            </div>
          </div>
        )}
        {showLive && (
          <div className="row bot">
            <div className="avatar-sm">
              <Bot size={16} />
            </div>
            <div className="bubble bot">
              <Markdown className="bubble-text chat-md">{liveText}</Markdown>
            </div>
          </div>
        )}
      </div>

      <Composer variant="chat" busy={busy} onSend={onSend} />
    </div>
  );
}

// Bulles + séparateurs de jour. Avant la fin de l'hydratation on rend un feed
// vide (évite un flash du message d'accueil suivi de l'historique rechargé) ;
// une fois hydraté et si le fil est vide, message d'accueil sobre.
function renderFeed(messages: ReturnType<typeof useHistory>["messages"], loaded: boolean) {
  if (messages.length === 0) {
    if (!loaded) return null;
    return (
      <div className="row bot">
        <div className="avatar-sm">
          <Bot size={16} />
        </div>
        <div className="bubble bot">
          <span className="bubble-text">
            On peut discuter ici sans que ça pollue ton dashboard. Vas-y.
          </span>
        </div>
      </div>
    );
  }
  const nodes: React.ReactNode[] = [];
  let lastDay: Date | null = null;
  messages.forEach((m, i) => {
    const d = m.createdAt ? new Date(m.createdAt) : new Date();
    if (!Number.isNaN(d.getTime()) && (lastDay === null || !sameDay(d, lastDay))) {
      nodes.push(<DaySeparator key={`sep-${m.id ?? i}`} label={formatDaySeparator(d)} />);
      lastDay = d;
    }
    nodes.push(<MessageBubble key={m.id ?? `live-${i}`} message={m} />);
  });
  return nodes;
}
