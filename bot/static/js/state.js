// ── État global partagé ───────────────────────────────────────────────────
// Les variables globales historiques de la PWA, exposées en exports nommés.
// Les bindings ES6 sont vivants : les lectures depuis les autres modules
// voient toujours la valeur courante. En revanche un binding importé n'est
// pas réassignable — les réassignations passent par les setters ci-dessous.
// Les objets/tableaux (chatHistory, newsState) sont mutés en place : const.

// ── Config ────────────────────────────────────────────────────────────────
export let API_KEY = "";
export const API_BASE = "";
export const PROFILE_NAME = "Arnaud";

// ── État ──────────────────────────────────────────────────────────────────
export let loading      = false;
export let attachment   = null;   // { b64, mediaType, preview }
export let dashboardData = null;
export const chatHistory  = [];     // [{role, text}]
// Pièce jointe spécifique au mode chat (séparée de `attachment` utilisée
// par la barre principale, pour que les deux vues ne s'écrasent pas).
export let chatAttachment = null;
// Card Actu : état persistant en mémoire (la card reste « fraîche » tant
// que la PWA est ouverte ; un reload de la page remet à zéro).
export const newsState   = { fetchedAt: null, loading: false, markdown: null };
// Card "Pour toi" : restitution des dépôts, fetch au tap (canal pull).
// `items` null = jamais chargé (la card reste neutre, sans signal entrant) ;
// [] = chargé mais rien à restituer ; [...] = items en attente d'action.
// Invalidé (remis à null) quand un dépôt/clôture remonte refresh_cards:["foryou"].
export const foryouState = { fetchedAt: null, items: null };

// ── Setters (réassignation depuis les autres modules) ─────────────────────
export function setApiKey(v) { API_KEY = v; }
export function setLoadingFlag(v) { loading = v; }
export function setAttachment(v) { attachment = v; }
export function setChatAttachment(v) { chatAttachment = v; }
export function setDashboardData(v) { dashboardData = v; }
