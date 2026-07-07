// ── Cache de session « Pour toi » (restitution des dépôts) ───────────────────
// Miroir du foryouState de bot/static/js/state.js : GET /foryou est un canal
// PULL qui fait FORMULER chaque item par le LLM → on ne le rappelle qu'au 1er
// tap de la session, puis on réaffiche le cache jusqu'à invalidation (nouveau
// dépôt ou clôture d'un souci, cf. invalidateCards(["foryou"]) du vanilla).
// État module-level (vit hors du cycle de montage des overlays, qui se
// démontent à la fermeture), comme la clé API mémoïsée de client.ts.

import type { ForYouItemResponse } from "../api/types";

let items: ForYouItemResponse[] | null = null;
let fetchedAt: string | null = null;

/** Snapshot courant du cache (items=null → jamais chargé / invalidé). */
export function getForYouCache(): {
  items: ForYouItemResponse[] | null;
  fetchedAt: string | null;
} {
  return { items, fetchedAt };
}

/** Mémorise la liste restituée (après fetch, ou après retrait local d'un item). */
export function setForYouCache(next: ForYouItemResponse[], at: string | null): void {
  items = next;
  fetchedAt = at;
}

/** Invalide le cache : force un refetch au prochain tap (dépôt/clôture). */
export function invalidateForYou(): void {
  items = null;
  fetchedAt = null;
}
