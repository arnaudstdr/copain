// ── État du mode dialogue (hors React) ──────────────────────────────────────
// Le fil de discussion doit SURVIVRE à la fermeture/réouverture de l'overlay
// chat dans une même session (comme le vanilla gardait `chatHistory` /
// `chatHistoryState` au niveau module de state.js). Or ChatView se démonte à la
// fermeture → l'état doit vivre hors React (même raison que la clé API et le
// cache « Pour toi », cf. PROGRESS.md). On expose un petit store externe
// consommé via `useSyncExternalStore`.
//
// Réinitialisé naturellement au rechargement de la page : au 1er tap suivant,
// `useHistory` réhydrate depuis `GET /history` (le backend persiste tout ce qui
// passe par `/ask/stream`).

import { useSyncExternalStore } from "react";

import type { Action } from "../api/types";

/** Bulle du fil : messages restaurés (avec `id` serveur) ou envoyés en session. */
export interface ChatMessage {
  id?: number; // présent pour les messages restaurés depuis /history
  role: "user" | "assistant";
  text: string;
  createdAt: string; // ISO 8601
  error?: boolean; // bulle d'erreur (message FR convivial, non persisté)
  imgSrc?: string; // aperçu (data URL) d'une photo jointe en session (non persisté)
  actions?: Action[]; // actions proposées (deep-links tappables, non persisté)
}

interface ChatState {
  messages: ChatMessage[]; // ordre chronologique croissant
  hasMore: boolean; // reste-t-il des pages plus anciennes à charger ?
  oldestId: number | null; // curseur de pagination (id du plus ancien chargé)
  loaded: boolean; // l'hydratation initiale a-t-elle eu lieu cette session ?
}

let state: ChatState = { messages: [], hasMore: false, oldestId: null, loaded: false };
const listeners = new Set<() => void>();

// Verrou synchrone d'hydratation : empêche le double-fetch (et le double
// prepend, non idempotent) du double-montage d'effet de React.StrictMode en dev
// (cf. piège « GET qui purge + StrictMode » de PROGRESS.md, ici décliné au
// prepend d'historique). Vit au niveau module → partagé entre les deux montages.
let hydrationStarted = false;

function emit(next: ChatState): void {
  state = next;
  listeners.forEach((l) => l());
}

/** Acquiert le verrou d'hydratation. Renvoie `false` si déjà pris. */
export function beginHydration(): boolean {
  if (hydrationStarted) return false;
  hydrationStarted = true;
  return true;
}

/** Fige le résultat de l'hydratation initiale (page la plus récente). */
export function setHydrated(restored: ChatMessage[], hasMore: boolean): void {
  emit({
    messages: [...restored, ...state.messages],
    hasMore,
    oldestId: restored.length ? (restored[0].id ?? null) : state.oldestId,
    loaded: true,
  });
}

/** Marque l'hydratation terminée sans messages restaurés (échec ou fil vide). */
export function markLoaded(): void {
  if (state.loaded) return;
  emit({ ...state, loaded: true });
}

/** Ajoute une page plus ancienne en tête (scroll infini vers le haut). */
export function prependOlder(older: ChatMessage[], hasMore: boolean): void {
  emit({
    ...state,
    messages: older.length ? [...older, ...state.messages] : state.messages,
    hasMore,
    oldestId: older.length ? (older[0].id ?? state.oldestId) : state.oldestId,
  });
}

/** Ajoute une bulle en fin de fil (message envoyé ou réponse). */
export function appendMessage(message: ChatMessage): void {
  emit({ ...state, messages: [...state.messages, message] });
}

/** Snapshot courant (référence stable tant que l'état ne change pas). */
export function getChatState(): ChatState {
  return state;
}

/** S'abonne aux mutations du store ; renvoie le désabonnement. */
export function subscribeChat(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Hook de lecture réactive de l'état du chat. */
export function useChatState(): ChatState {
  return useSyncExternalStore(subscribeChat, getChatState);
}
