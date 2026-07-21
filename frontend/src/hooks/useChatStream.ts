// ── Envoi streamé du mode dialogue (POST /ask/stream) ───────────────────────
// Pousse la bulle utilisateur, ouvre le flux SSE via `streamAsk`, accumule les
// `delta` (et gère `replace` = remplace tout, `done` = intent + refresh_cards,
// `error` = message FR convivial), puis fige la bulle assistant. Portage de la
// partie streaming de `chatSend` (bot/static/js/chat.js). Le texte en cours de
// frappe (`liveText`) est un état transitoire d'affichage : il n'entre dans le
// fil (store) qu'à la fin. Chemin TEXTE uniquement (photos → /ask/image step 08).

import { useCallback, useEffect, useRef, useState } from "react";
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
  // Contrôleur du flux SSE en cours : sert à annuler le fetch/reader au
  // démontage de l'écran Chat (bascule d'onglet mid-stream). Sans ça, un stream
  // orphelin continuerait de pousser des bulles dans le `chatStore` partagé,
  // s'entrelaçant avec un éventuel nouvel envoi.
  const abortRef = useRef<AbortController | null>(null);

  // Annule le flux en vol au démontage du hook (changement d'onglet).
  useEffect(() => () => abortRef.current?.abort(), []);

  const send = useCallback(
    async (raw: string, think = false) => {
      const text = raw.trim();
      if (!text || streaming) return;

      const now = new Date().toISOString();
      appendMessage({ role: "user", text, createdAt: now });
      setStreaming(true);
      setLiveText("");

      const controller = new AbortController();
      abortRef.current = controller;

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
          controller.signal,
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
        // Annulation volontaire (démontage) : ne pas polluer le fil partagé
        // d'une bulle d'erreur ni toucher un état désormais démonté.
        if (controller.signal.aborted) return;
        appendMessage({ role: "assistant", text: FALLBACK, error: true, createdAt: new Date().toISOString() });
      } finally {
        if (!controller.signal.aborted) {
          setStreaming(false);
          setLiveText("");
        }
      }
    },
    [streaming, onRefreshCards],
  );

  return { send, streaming, liveText };
}
