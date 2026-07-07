// ── Historique du mode dialogue (GET /history) ──────────────────────────────
// Hydrate le fil depuis la persistance serveur à la 1re ouverture de session,
// puis charge les pages plus anciennes en scroll infini (curseur `before_id`).
// Seul `/ask/stream` est historisé côté backend (ni Siri, ni photos, ni bulle
// éphémère) — le front ne fait que lire. Portage de `hydrateHistory` /
// `loadOlder` de bot/static/js/chat.js (l'état vit dans lib/chatStore).

import { useCallback, useEffect, useRef } from "react";
import { apiGet } from "../api/client";
import type { ChatHistoryResponse, ChatMessageItem } from "../api/types";
import {
  beginHydration,
  markLoaded,
  prependOlder,
  setHydrated,
  useChatState,
  type ChatMessage,
} from "../lib/chatStore";

const PAGE_SIZE = 50;

// Miroir des lignes serveur vers le modèle de bulle interne.
function toMessages(items: ChatMessageItem[]): ChatMessage[] {
  return items.map((m) => ({
    id: m.id,
    role: m.role === "user" ? "user" : "assistant",
    text: m.content,
    createdAt: m.created_at,
  }));
}

export function useHistory() {
  const { messages, hasMore, oldestId, loaded } = useChatState();
  // Garde-fou de non-réentrance du chargement d'une page plus ancienne.
  const loadingOlderRef = useRef(false);

  // Hydratation initiale : une seule fois par session (verrou `beginHydration`
  // côté store contre le double-montage StrictMode). On NE coupe PAS le fetch au
  // démontage : fermer l'overlay ne doit pas perdre l'hydratation — l'écriture
  // se fait dans le store externe, sûre même sans listener monté.
  useEffect(() => {
    if (loaded || !beginHydration()) return;
    void (async () => {
      try {
        const data = await apiGet<ChatHistoryResponse>(`/history?limit=${PAGE_SIZE}`);
        setHydrated(toMessages(data.messages), data.has_more);
      } catch {
        markLoaded(); // réaffichage non critique : on garde un fil vide
      }
    })();
  }, [loaded]);

  // Charge une page plus ancienne. Renvoie `true` si des messages ont été
  // ajoutés en tête (permet à ChatView de préserver la position de lecture).
  const loadOlder = useCallback(async (): Promise<boolean> => {
    if (loadingOlderRef.current || !hasMore || oldestId == null) return false;
    loadingOlderRef.current = true;
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), before_id: String(oldestId) });
      const data = await apiGet<ChatHistoryResponse>(`/history?${params}`);
      const older = toMessages(data.messages);
      prependOlder(older, data.has_more);
      return older.length > 0;
    } catch {
      return false; // silencieux : réessai au prochain scroll vers le haut
    } finally {
      loadingOlderRef.current = false;
    }
  }, [hasMore, oldestId]);

  return { messages, loaded, loadOlder };
}
