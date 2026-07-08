// ── Mode dialogue (vue plein écran) ─────────────────────────────────────────
// Bascule depuis l'icône 💬 du header. Feed de bulles (historique persisté +
// échanges de la session), envoi streamé SSE sur POST /ask/stream, rendu
// markdown des réponses. Scroll infini vers le haut (pages plus anciennes) avec
// séparateurs de jour et préservation de la position de lecture au prepend.
// Portage de bot/static/js/chat.js. Le composer (partagé avec le dashboard,
// step 08) route le texte vers le streaming et la photo vers /ask/image
// (réponse en un bloc, bulles poussées dans le fil comme le vanilla).

import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { Bot, ChevronLeft } from "lucide-react";
import { useHistory } from "../../hooks/useHistory";
import { useChatStream } from "../../hooks/useChatStream";
import { askImage } from "../../api/client";
import { appendMessage } from "../../lib/chatStore";
import { formatDaySeparator, sameDay } from "../../lib/format";
import { Markdown } from "../Markdown";
import { Composer } from "../Composer";
import type { Attachment } from "../Composer";
import { DaySeparator } from "./DaySeparator";
import { MessageBubble } from "./MessageBubble";

interface Props {
  onClose: () => void;
  onRefreshCards: (cards: string[]) => void;
}

export function ChatView({ onClose, onRefreshCards }: Props) {
  const { messages, loaded, loadOlder } = useHistory();
  const { send, streaming, liveText, hasDelta } = useChatStream({ onRefreshCards });
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
    if (!feed || feed.scrollTop >= 40) return;
    // Mémorise la hauteur avant le prepend ; annulé si loadOlder n'a rien ajouté.
    prevHeightRef.current = feed.scrollHeight;
    void loadOlder().then((didPrepend) => {
      if (!didPrepend) prevHeightRef.current = null;
    });
  }, [loadOlder]);

  // Photo : /ask/image (pas de streaming). Bulle utilisateur (avec aperçu) puis
  // réponse ou message d'erreur, poussés dans le fil. Portage de chatSend (att).
  const sendChatImage = useCallback(
    async (text: string, att: Attachment) => {
      appendMessage({ role: "user", text, imgSrc: att.preview, createdAt: new Date().toISOString() });
      setImgBusy(true);
      try {
        const body = await askImage(text, att.b64, att.mediaType);
        appendMessage({
          role: "assistant",
          text: body.response,
          actions: body.actions?.length ? body.actions : undefined,
          createdAt: new Date().toISOString(),
        });
        if (body.refresh_cards.length > 0) onRefreshCards(body.refresh_cards);
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
    [onRefreshCards],
  );

  const onSend = useCallback(
    (text: string, attachment: Attachment | null) => {
      if (attachment) void sendChatImage(text, attachment);
      else void send(text, think);
    },
    [sendChatImage, send, think],
  );

  const busy = streaming || imgBusy;

  return (
    <div id="chat-view">
      <header>
        <button className="header-btn" title="Retour" type="button" onClick={onClose}>
          <ChevronLeft size={18} />
        </button>
        <div className="greeting" style={{ marginLeft: 8 }}>
          <div className="greeting-name">Conversation</div>
          <div className="greeting-date">Mode dialogue</div>
        </div>
      </header>

      <div className="chat-feed" ref={feedRef} onScroll={onScroll}>
        {renderFeed(messages, loaded)}
        {busy && !hasDelta && (
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
        {streaming && hasDelta && (
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

      <Composer
        variant="chat"
        busy={busy}
        onSend={onSend}
        think={think}
        onToggleThink={() => setThink((v) => !v)}
      />
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
