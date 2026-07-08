// ── Envoi streamé du mode dialogue (POST /ask/stream) ───────────────────────
// Pousse la bulle utilisateur, ouvre le flux SSE via `streamAsk`, accumule les
// `delta` (et gère `replace` = remplace tout, `done` = intent + refresh_cards,
// `error` = message FR convivial), puis fige la bulle assistant. Portage de la
// partie streaming de `chatSend` (bot/static/js/chat.js). Le texte en cours de
// frappe (`liveText`) est un état transitoire d'affichage : il n'entre dans le
// fil (store) qu'à la fin. Chemin TEXTE uniquement (photos → /ask/image step 08).

import { useCallback, useState } from "react";
import { streamAsk } from "../api/client";
import type { Action } from "../api/types";
import { appendMessage } from "../lib/chatStore";

const FALLBACK = "Impossible de joindre Copain.";

interface UseChatStreamOptions {
  // Appliqué à la frame `done` : rafraîchit les cards impactées (dashboard,
  // invalidation « Pour toi »…). Câblé par App.
  onRefreshCards: (cards: string[]) => void;
}

export function useChatStream({ onRefreshCards }: UseChatStreamOptions) {
  const [streaming, setStreaming] = useState(false);
  const [liveText, setLiveText] = useState(""); // texte assistant accumulé (live)

  const send = useCallback(
    async (raw: string, think = false) => {
      const text = raw.trim();
      if (!text || streaming) return;

      const now = new Date().toISOString();
      appendMessage({ role: "user", text, createdAt: now });
      setStreaming(true);
      setLiveText("");

      let acc = "";
      let actions: Action[] = [];
      let streamError: string | null = null;
      try {
        await streamAsk(
          text,
          {
            onDelta(t) {
              acc += t;
              setLiveText(acc);
            },
            onReplace(t) {
              acc = t;
              setLiveText(acc);
            },
            onDone(_intent, refreshCards, doneActions) {
              if (refreshCards.length > 0) onRefreshCards(refreshCards);
              actions = doneActions;
            },
            onError(t) {
              streamError = t || FALLBACK;
            },
          },
          think,
        );
        appendMessage(
          streamError
            ? { role: "assistant", text: streamError, error: true, createdAt: new Date().toISOString() }
            : {
                role: "assistant",
                text: acc,
                actions: actions.length > 0 ? actions : undefined,
                createdAt: new Date().toISOString(),
              },
        );
      } catch {
        appendMessage({ role: "assistant", text: FALLBACK, error: true, createdAt: new Date().toISOString() });
      } finally {
        setStreaming(false);
        setLiveText("");
      }
    },
    [streaming, onRefreshCards],
  );

  return { send, streaming, liveText };
}
