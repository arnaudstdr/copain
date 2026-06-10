// ── Overlays : notifications, tâches (swipe), météo, évènements ───────────
// Cycle d'import dashboard ↔ overlays accepté (cf. PROGRESS.md) : les cards
// tappables ouvrent les overlays, et les overlays rafraîchissent le
// dashboard à la fermeture. Bénin : fonctions hoistées, appelées au runtime.
import { API_KEY, API_BASE, dashboardData, foryouState } from "./state.js?v=13";
import {
  el,
  lucideNode,
  formatHM, formatRelativeDay, formatDateTime, isAllDayEvent,
  showToast,
} from "./ui.js?v=13";
import { loadDashboard, renderBellBadge } from "./dashboard.js?v=13";

// ── Notifications panel ───────────────────────────────────────────────────
export async function openNotifs() {
  document.getElementById("notif-overlay").classList.remove("hidden");
  const list = document.getElementById("notif-list");
  list.innerHTML = '<div class="panel-empty">Chargement…</div>';
  try {
    const res = await fetch(`${API_BASE}/notifications`, { headers: { "X-API-Key": API_KEY } });
    const data = await res.json();
    const items = data.notifications ?? [];
    if (items.length === 0) {
      list.innerHTML = '<div class="panel-empty">Aucune notification en attente</div>';
    } else {
      list.innerHTML = "";
      items.forEach(n => {
        const item = el("div", "notif-item");
        item.appendChild(el("div", "notif-text", n.text));
        item.appendChild(el("div", "notif-time", formatDateTime(new Date(n.created_at))));
        list.appendChild(item);
      });
    }
    // Le GET a déjà marqué les notifs comme lues côté backend → maj du badge.
    renderBellBadge(0);
    if (dashboardData) dashboardData.unread_notifications = 0;
  } catch (e) {
    list.innerHTML = '<div class="panel-empty">Impossible de charger</div>';
  }
}
export function closeNotifs() {
  document.getElementById("notif-overlay").classList.add("hidden");
  loadDashboard();
}

// ── Tâches (overlay) ─────────────────────────────────────────────────────
export async function openTasks() {
  document.getElementById("tasks-overlay").classList.remove("hidden");
  const list = document.getElementById("tasks-list");
  list.innerHTML = '<div class="panel-empty">Chargement…</div>';
  try {
    const res = await fetch(`${API_BASE}/tasks`, { headers: { "X-API-Key": API_KEY } });
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();
    renderTasks(data.tasks || []);
  } catch (e) {
    list.innerHTML = '<div class="panel-empty">Impossible de charger</div>';
  }
}

export function closeTasks() {
  document.getElementById("tasks-overlay").classList.add("hidden");
  // Rafraîchit la card du dashboard (count + première tâche peuvent avoir bougé).
  loadDashboard();
}

// ── Météo (overlay) ──────────────────────────────────────────────────────
export async function openWeather() {
  document.getElementById("weather-overlay").classList.remove("hidden");
  const list = document.getElementById("weather-list");
  list.innerHTML = '<div class="panel-empty">Chargement…</div>';
  try {
    const res = await fetch(`${API_BASE}/weather/forecast?days=7&hours=24`, {
      headers: { "X-API-Key": API_KEY }
    });
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();
    const titleEl = document.getElementById("weather-title");
    titleEl.textContent = "";
    titleEl.appendChild(lucideNode("cloud-sun", undefined, "lucide-text"));
    titleEl.appendChild(document.createTextNode(`Météo · ${data.city}`));
    renderWeather(data);
  } catch (e) {
    list.innerHTML = '<div class="panel-empty">Impossible de charger</div>';
  }
}

export function closeWeather() {
  document.getElementById("weather-overlay").classList.add("hidden");
}

function renderWeather(data) {
  const list = document.getElementById("weather-list");
  list.innerHTML = "";

  // Section horaire (24h glissantes), strip horizontal.
  if (data.hourly && data.hourly.length > 0) {
    list.appendChild(el("div", "weather-section-title", "Heure par heure"));
    const strip = el("div", "weather-hourly-strip");
    data.hourly.forEach(h => strip.appendChild(makeHourCell(h)));
    list.appendChild(strip);
  }

  // Section quotidienne (7 jours).
  if (data.daily && data.daily.length > 0) {
    list.appendChild(el("div", "weather-section-title", "Prochains jours"));
    data.daily.forEach(d => list.appendChild(makeDayRow(d)));
  }
}

function makeHourCell(h) {
  const cell = el("div", "weather-hour");
  const time = new Date(h.time);
  cell.appendChild(el("div", "weather-hour-time", formatHM(time)));
  const iconWrap = el("div", "weather-hour-icon");
  iconWrap.appendChild(lucideNode(weatherIconName(h.description), 20));
  cell.appendChild(iconWrap);
  cell.appendChild(el("div", "weather-hour-temp", `${Math.round(h.temp_c)}°`));
  if (h.precipitation_probability_pct >= 30 || h.precipitation_mm > 0.1) {
    const txt = h.precipitation_mm > 0.1
      ? `${h.precipitation_mm.toFixed(1)} mm`
      : `${h.precipitation_probability_pct}%`;
    cell.appendChild(el("div", "weather-hour-precip", txt));
  }
  return cell;
}

function makeDayRow(d) {
  const row = el("div", "weather-day");
  const date = new Date(d.date);
  row.appendChild(el("div", "weather-day-label", formatRelativeDay(date)));
  const iconWrap = el("div", "weather-day-icon");
  iconWrap.appendChild(lucideNode(weatherIconName(d.description), 20));
  row.appendChild(iconWrap);
  row.appendChild(el("div", "weather-day-desc", d.description));
  row.appendChild(el("div", "weather-day-temps", `${Math.round(d.temp_min)}° / ${Math.round(d.temp_max)}°`));
  if (d.precipitation_mm > 0.1) {
    row.appendChild(el("div", "weather-day-precip", `${d.precipitation_mm.toFixed(1)} mm`));
  }
  return row;
}

function weatherIconName(description) {
  const d = (description || "").toLowerCase();
  if (d.includes("orage")) return "cloud-lightning";
  if (d.includes("neige") || d.includes("verglaç")) return "snowflake";
  if (d.includes("pluie") || d.includes("averse")) return "cloud-rain";
  if (d.includes("bruine")) return "cloud-drizzle";
  if (d.includes("brouillard")) return "cloud-fog";
  if (d.includes("couvert")) return "cloud";
  if (d.includes("partiel") || d.includes("plutôt dégagé")) return "cloud-sun";
  return "sun";
}

// ── Évènements (overlay) ─────────────────────────────────────────────────
export async function openEvents() {
  document.getElementById("events-overlay").classList.remove("hidden");
  const list = document.getElementById("events-list");
  list.innerHTML = '<div class="panel-empty">Chargement…</div>';
  try {
    const res = await fetch(`${API_BASE}/events?days=7`, {
      headers: { "X-API-Key": API_KEY }
    });
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();
    renderEvents(data.events || []);
  } catch (e) {
    list.innerHTML = '<div class="panel-empty">Impossible de charger</div>';
  }
}

export function closeEvents() {
  document.getElementById("events-overlay").classList.add("hidden");
}

function renderEvents(events) {
  const list = document.getElementById("events-list");
  list.innerHTML = "";
  if (events.length === 0) {
    list.innerHTML = '<div class="panel-empty">Aucun évènement à venir</div>';
    return;
  }

  // Regrouper par date locale (YYYY-MM-DD).
  // toISOString() est en UTC : un all-day Paris (ex. 22 mai 00:00 +02:00)
  // remonte au 21 mai 22:00Z et serait groupé la veille. On lit les
  // composantes locales pour rester sur le bon jour côté utilisateur.
  const groups = new Map();
  events.forEach(e => {
    const d = new Date(e.start);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(e);
  });

  for (const [key, group] of groups) {
    const sample = new Date(key + "T00:00:00");
    const dayGroup = el("div", "events-day-group");
    dayGroup.appendChild(el("div", "events-day-label", formatRelativeDay(sample)));
    group.forEach(e => dayGroup.appendChild(makeEventItem(e)));
    list.appendChild(dayGroup);
  }
}

function makeEventItem(e) {
  const item = el("div", "event-item");
  const start = new Date(e.start);
  const end = new Date(e.end);
  const timeLabel = isAllDayEvent(start, end)
    ? "Toute la journée"
    : `${formatHM(start)} – ${formatHM(end)}`;
  item.appendChild(el("div", "event-time", timeLabel));
  item.appendChild(el("div", "event-title", e.title));
  if (e.location) {
    const loc = el("div", "event-location");
    loc.appendChild(lucideNode("map-pin", 12));
    loc.appendChild(document.createTextNode(e.location));
    item.appendChild(loc);
  }
  item.appendChild(el("div", "event-calendar", e.calendar_name));
  return item;
}

// ── Pour toi (restitution des dépôts) ─────────────────────────────────────
export async function openForYou() {
  document.getElementById("foryou-overlay").classList.remove("hidden");
  // Déjà chargé dans cette session → on réaffiche le cache sans refetch
  // (canal pull, on ne sollicite le serveur qu'au premier tap ou après
  // invalidation par un dépôt/clôture).
  if (foryouState.items !== null) {
    renderForYou(foryouState.items);
    return;
  }
  const list = document.getElementById("foryou-list");
  list.innerHTML = '<div class="panel-empty">Chargement…</div>';
  try {
    const res = await fetch(`${API_BASE}/foryou`, { headers: { "X-API-Key": API_KEY } });
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();
    foryouState.items = data.items || [];
    foryouState.fetchedAt = data.fetched_at;
    renderForYou(foryouState.items);
  } catch (e) {
    // On laisse items à null pour permettre un nouveau tap (la card reste idle).
    list.innerHTML = '<div class="panel-empty">Impossible de charger</div>';
    showToast("Impossible de charger");
  }
}

export function closeForYou() {
  document.getElementById("foryou-overlay").classList.add("hidden");
  loadDashboard();
}

function renderForYou(items) {
  const list = document.getElementById("foryou-list");
  list.innerHTML = "";
  if (!items || items.length === 0) {
    // État apaisant, jamais une erreur : c'est une bonne nouvelle.
    list.innerHTML = '<div class="panel-empty">Rien en attente — tout est rangé.</div>';
    return;
  }
  items.forEach(item => list.appendChild(makeForYouItem(item)));
}

function makeForYouItem(item) {
  const row = el("div", "foryou-item");
  row.appendChild(el("div", "foryou-message", item.message));
  const actions = el("div", "foryou-actions");
  const done = el("button", "foryou-btn primary", "C'est réglé");
  done.onclick = () => resolveForYou(item, row);
  const keep = el("button", "foryou-btn ghost", "Garder");
  keep.onclick = () => removeForYouItem(item, row);
  actions.appendChild(done);
  actions.appendChild(keep);
  row.appendChild(actions);
  return row;
}

async function resolveForYou(item, row) {
  if (row.classList.contains("foryou-resolving")) return;
  row.classList.add("foryou-resolving");
  const ids = item.thought_ids || [];
  // Un item « boucle » porte plusieurs dépôts ouverts : on les clôt tous.
  // /close est idempotent → un retour après échec partiel est sans danger.
  const results = await Promise.allSettled(
    ids.map(id =>
      fetch(`${API_BASE}/thoughts/${id}/close`, {
        method: "POST",
        headers: { "X-API-Key": API_KEY },
      }).then(r => { if (!r.ok) throw new Error(`${r.status}`); })
    )
  );
  const failed = results.filter(r => r.status === "rejected").length;
  if (failed > 0) {
    row.classList.remove("foryou-resolving");
    showToast("Certains dépôts n'ont pas pu être clôturés");
    return;
  }
  removeForYouItem(item, row);
}

function removeForYouItem(item, row) {
  // Retire de l'état en mémoire pour qu'il ne réapparaisse pas cette session
  // (« Garder » = masquage local sans réseau ; « C'est réglé » = après clôture).
  if (foryouState.items) {
    foryouState.items = foryouState.items.filter(i => i !== item);
  }
  row.style.transition = "opacity 0.25s, max-height 0.25s, padding 0.25s, margin 0.25s";
  row.style.opacity = "0";
  row.style.maxHeight = "0";
  row.style.padding = "0";
  row.style.margin = "0";
  setTimeout(() => {
    row.remove();
    if (foryouState.items && foryouState.items.length === 0) {
      renderForYou([]); // bascule sur l'état apaisant
    }
  }, 260);
}

function renderTasks(tasks) {
  const list = document.getElementById("tasks-list");
  list.innerHTML = "";
  if (tasks.length === 0) {
    list.innerHTML = '<div class="panel-empty">Aucune tâche en cours</div>';
    return;
  }
  tasks.forEach(t => list.appendChild(makeTaskRow(t)));
}

function makeTaskRow(t) {
  const row = el("div", "task-row");
  row.dataset.taskId = String(t.id);

  // Bouton "Supprimer" révélé par swipe gauche.
  const del = el("div", "task-row-delete", "Supprimer");
  del.onclick = (e) => { e.stopPropagation(); deleteTask(t.id, row); };
  row.appendChild(del);

  // Contenu principal de la ligne (cliquable, swipable).
  const inner = el("div", "task-row-inner");

  const check = el("div", "task-check");
  check.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>';
  check.onclick = (e) => { e.stopPropagation(); completeTask(t.id, row, check); };
  inner.appendChild(check);

  const body = el("div", "task-body");
  body.appendChild(el("div", "task-content", t.content));
  if (t.due_at) {
    const due = new Date(t.due_at);
    const isOverdue = due < new Date();
    const dueEl = el("div", "task-due" + (isOverdue ? " overdue" : ""), formatTaskDue(due));
    body.appendChild(dueEl);
  }
  inner.appendChild(body);

  // Tap court sur la ligne (en dehors de la checkbox) replie le swipe si ouvert.
  inner.onclick = () => { row.classList.remove("swiped"); };

  row.appendChild(inner);
  attachSwipe(row, inner);
  return row;
}

function attachSwipe(row, inner) {
  let startX = 0;
  let currentX = 0;
  let dragging = false;
  let opened = false;

  const onStart = (e) => {
    const t = e.touches ? e.touches[0] : e;
    startX = t.clientX;
    currentX = startX;
    dragging = true;
    inner.style.transition = "none";
  };
  const onMove = (e) => {
    if (!dragging) return;
    const t = e.touches ? e.touches[0] : e;
    currentX = t.clientX;
    const dx = Math.min(0, currentX - startX); // n'autorise que swipe gauche
    const offset = opened ? -84 + dx : dx;
    inner.style.transform = `translateX(${Math.max(-84, offset)}px)`;
  };
  const onEnd = () => {
    if (!dragging) return;
    dragging = false;
    inner.style.transition = "";
    inner.style.transform = "";
    const dx = currentX - startX;
    // Seuil 40px : si swipe gauche dépasse, on ouvre. Si swipe droit suffit, on referme.
    if (!opened && dx < -40) {
      row.classList.add("swiped");
      opened = true;
    } else if (opened && dx > 30) {
      row.classList.remove("swiped");
      opened = false;
    } else {
      // Snap back à l'état précédent.
      if (opened) row.classList.add("swiped"); else row.classList.remove("swiped");
    }
  };

  inner.addEventListener("touchstart", onStart, { passive: true });
  inner.addEventListener("touchmove", onMove, { passive: true });
  inner.addEventListener("touchend", onEnd);
  // Support souris pour le desktop / test.
  inner.addEventListener("mousedown", onStart);
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onEnd);
}

async function completeTask(id, row, check) {
  if (row.classList.contains("task-completing")) return;
  row.classList.add("task-completing");
  check.classList.add("checked");
  try {
    const res = await fetch(`${API_BASE}/tasks/${id}/complete`, {
      method: "POST",
      headers: { "X-API-Key": API_KEY }
    });
    if (!res.ok) throw new Error(`${res.status}`);
    // Animation de disparition puis retrait du DOM.
    setTimeout(() => {
      row.style.transition = "opacity 0.25s, max-height 0.25s, padding 0.25s, margin 0.25s";
      row.style.opacity = "0";
      row.style.maxHeight = "0";
      row.style.padding = "0";
      row.style.margin = "0";
      setTimeout(() => row.remove(), 260);
    }, 200);
  } catch (e) {
    row.classList.remove("task-completing");
    check.classList.remove("checked");
    showToast("Impossible de cocher");
  }
}

async function deleteTask(id, row) {
  try {
    const res = await fetch(`${API_BASE}/tasks/${id}`, {
      method: "DELETE",
      headers: { "X-API-Key": API_KEY }
    });
    if (!res.ok) throw new Error(`${res.status}`);
    row.style.transition = "opacity 0.2s, max-height 0.2s, padding 0.2s, margin 0.2s";
    row.style.opacity = "0";
    row.style.maxHeight = "0";
    row.style.padding = "0";
    row.style.margin = "0";
    setTimeout(() => row.remove(), 210);
  } catch (e) {
    showToast("Impossible de supprimer");
  }
}

function formatTaskDue(d) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const dayDiff = Math.floor((d.getTime() - today.getTime()) / (24 * 3600 * 1000));
  const hm = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (dayDiff < 0) {
    const ago = Math.abs(dayDiff);
    return `En retard de ${ago} jour${ago > 1 ? "s" : ""}`;
  }
  if (dayDiff === 0) return `Aujourd'hui ${hm}`;
  if (dayDiff === 1) return `Demain ${hm}`;
  return d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" }) + ` ${hm}`;
}
