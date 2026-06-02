// ── Helpers UI partagés : DOM, icônes Lucide, dates, toast/éphémère ───────
// Cycle d'import ui ↔ markdown accepté (cf. PROGRESS.md) : showEphemeral
// rend du markdown, et renderMarkdown utilise escHtml/lucideSvg d'ici.
// Bénin : fonctions hoistées, appelées uniquement au runtime.
import { renderMarkdown } from "./markdown.js";

// ── UI helpers ────────────────────────────────────────────────────────────
export function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function el(tag, cls, content) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (content !== undefined && content !== null) {
    if (content instanceof Node) n.appendChild(content);
    else n.textContent = content;
  }
  return n;
}

// ── Icônes Lucide ─────────────────────────────────────────────────────────
// Contenu SVG (path/circle/line/polyline) extrait de la lib Lucide. Chaque
// icône hérite de currentColor pour s'aligner sur la couleur de texte du
// conteneur (ex. .card-icon → var(--text2)). Le viewBox est fixe 24×24.
const LUCIDE_ICONS = {
  "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  "check": '<polyline points="20 6 9 17 4 12"/>',
  "bell": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  "list-checks": '<path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/>',
  "calendar": '<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/>',
  "wallet": '<path d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-2"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4Z"/>',
  "newspaper": '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/><path d="M10 6h8v4h-8V6Z"/>',
  "alert-triangle": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  "map-pin": '<path d="M20 10c0 7-8 13-8 13s-8-6-8-13a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
  "bot": '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>',
  "cloud-sun": '<path d="M12 2v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="M20 12h2"/><path d="m19.07 4.93-1.41 1.41"/><path d="M15.947 12.65a4 4 0 0 0-5.925-4.128"/><path d="M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z"/>',
  "cloud-lightning": '<path d="M6 16.326A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 .5 8.973"/><path d="m13 12-3 5h4l-3 5"/>',
  "snowflake": '<line x1="2" x2="22" y1="12" y2="12"/><line x1="12" x2="12" y1="2" y2="22"/><path d="m20 16-4-4 4-4"/><path d="m4 8 4 4-4 4"/><path d="m16 4-4 4-4-4"/><path d="m8 20 4-4 4 4"/>',
  "cloud-rain": '<path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242"/><path d="M16 14v6"/><path d="M8 14v6"/><path d="M12 16v6"/>',
  "cloud-drizzle": '<path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242"/><path d="M8 19v1"/><path d="M8 14v1"/><path d="M16 19v1"/><path d="M16 14v1"/><path d="M12 21v1"/><path d="M12 16v1"/>',
  "cloud-fog": '<path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242"/><path d="M16 17H7"/><path d="M17 21H9"/>',
  "cloud": '<path d="M17.5 19a4.5 4.5 0 1 0-1.41-8.775 5.5 5.5 0 0 0-10.7 1.9A4.5 4.5 0 0 0 6.5 19Z"/>',
  "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
};
// Retourne une chaîne HTML SVG pour insertion via innerHTML.
export function lucideSvg(name, size, extraClass) {
  const inner = LUCIDE_ICONS[name];
  if (!inner) return "";
  const cls = "lucide lucide-" + name + (extraClass ? " " + extraClass : "");
  const dim = size ? ` width="${size}" height="${size}"` : "";
  return `<svg class="${cls}"${dim} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
}
// Retourne un nœud DOM pour appendChild direct.
export function lucideNode(name, size, extraClass) {
  const wrap = document.createElement("span");
  wrap.innerHTML = lucideSvg(name, size, extraClass);
  return wrap.firstChild;
}
export function makeHead(iconName, label) {
  const head = el("div", "card-head");
  const iconWrap = el("div", "card-icon");
  iconWrap.appendChild(lucideNode(iconName, 16));
  head.appendChild(iconWrap);
  head.appendChild(el("div", "card-label", label));
  return head;
}

// ── Date helpers ──────────────────────────────────────────────────────────
export function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}
export function formatHM(d) {
  return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}
export function formatRelativeDay(d) {
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  if (sameDay(d, tomorrow)) return "Demain";
  return d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" });
}
export function formatDateTime(d) {
  return d.toLocaleString("fr-FR", { weekday: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
export function isAllDayEvent(start, end) {
  // All-day iCloud : DTSTART/DTEND alignés sur minuit local, durée
  // multiple de 24 h (cas d'un jour : 24 h ; cas multi-jours : 48 h, …).
  if (start.getHours() !== 0 || start.getMinutes() !== 0 || start.getSeconds() !== 0) return false;
  if (end.getHours() !== 0 || end.getMinutes() !== 0 || end.getSeconds() !== 0) return false;
  const diffMs = end.getTime() - start.getTime();
  return diffMs > 0 && diffMs % 86400000 === 0;
}
export function formatRelativeAge(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const ageMin = Math.max(0, Math.floor((Date.now() - d.getTime()) / 60_000));
  if (ageMin < 1) return "à l'instant";
  if (ageMin < 60) return `il y a ${ageMin} min`;
  const ageH = Math.floor(ageMin / 60);
  if (ageH < 24) return `il y a ${ageH} h`;
  return `il y a ${Math.floor(ageH / 24)} j`;
}

// ── Bulle éphémère ────────────────────────────────────────────────────────
// Timer interne au module (pas dans state.js) : seul ce module y touche,
// et ça évite un cycle d'import ui ↔ state.
let ephemeralTimer = null;

export function showEphemeral(text, isError) {
  const e = document.getElementById("ephemeral");
  e.textContent = "";
  e.classList.toggle("chat-md", !isError);
  if (isError) {
    e.appendChild(lucideNode("alert-triangle", 16, "lucide-warn"));
    e.appendChild(document.createTextNode(" " + text));
  } else {
    // Réponse du bot : markdown rendu (échappé HTML dans renderMarkdown).
    e.innerHTML = renderMarkdown(text);
  }
  e.classList.remove("hidden");
  e.style.borderColor = isError ? "var(--red)" : "var(--border2)";
  clearTimeout(ephemeralTimer);
  ephemeralTimer = setTimeout(hideEphemeral, 8000);
}
export function hideEphemeral() {
  document.getElementById("ephemeral").classList.add("hidden");
  clearTimeout(ephemeralTimer);
}

// ── Toast ─────────────────────────────────────────────────────────────────
let toastTimer = null;

export function showToast(msg) {
  document.getElementById("toast")?.remove();
  clearTimeout(toastTimer);
  const t = el("div", "", msg);
  t.id = "toast";
  document.getElementById("app").appendChild(t);
  toastTimer = setTimeout(() => t.remove(), 1800);
}
