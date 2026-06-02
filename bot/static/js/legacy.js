// ── Imports ───────────────────────────────────────────────────────────────
import {
  API_KEY, API_BASE, PROFILE_NAME,
  loading, setLoadingFlag,
  attachment, setAttachment,
  chatAttachment, setChatAttachment,
  dashboardData,
  chatHistory,
} from "./state.js";
import {
  el,
  lucideNode,
  formatHM, formatRelativeDay, formatDateTime, isAllDayEvent,
  showToast, showEphemeral, hideEphemeral,
} from "./ui.js";
import { callText, callImage, callTextStream } from "./api.js";
import { renderMarkdown, closeMarkdownView } from "./markdown.js";
import { loadDashboard, flashCards, renderBellBadge } from "./dashboard.js";

// ── État local (déménage dans composer.js au step 05) ────────────────────
let recognition  = null;

export function renderGreeting() {
  document.getElementById("greeting-name").textContent = `Bonjour ${PROFILE_NAME}`;
  const d = new Date();
  const opts = { weekday: "long", day: "numeric", month: "long" };
  document.getElementById("greeting-date").textContent = d.toLocaleDateString("fr-FR", opts);
}

// ── Envoi /ask ────────────────────────────────────────────────────────────
async function triggerAsk(prefill) {
  document.getElementById("msg-input").value = prefill;
  updateSendBtn();
  await send();
}

async function send() {
  const text = document.getElementById("msg-input").value.trim();
  if ((!text && !attachment) || loading) return;

  setLoading(true);
  const att = attachment;
  removeAttachment();
  document.getElementById("msg-input").value = "";
  autoResize(document.getElementById("msg-input"));
  updateSendBtn();

  try {
    const body = att
      ? await callImage(text, att)
      : await callText(text);
    handleAskResponse(body, text);
  } catch (e) {
    showEphemeral("Impossible de joindre Copain. Vérifie Tailscale.", true);
  } finally {
    setLoading(false);
  }
}

function handleAskResponse(body, userText) {
  const intent = body.intent || "answer";
  const refresh = body.refresh_cards || [];

  // Mode action : toast court + rafraîchissement des cards concernées
  if (refresh.length > 0) {
    showToast(actionToast(intent));
    loadDashboard().then(() => flashCards(refresh));
  } else {
    // Mode question : on affiche la réponse texte en éphémère
    showEphemeral(body.response, false);
  }
}

function actionToast(intent) {
  let label;
  switch (intent) {
    case "task":   label = "Tâche ajoutée"; break;
    case "event":  label = "Évènement créé"; break;
    case "feed":   label = "Flux mis à jour"; break;
    case "memory": label = "Noté en mémoire"; break;
    case "expense":label = "Saisie enregistrée"; break;
    default:       label = "Fait";
  }
  const wrap = el("span", "toast-content");
  wrap.appendChild(lucideNode("check", 14));
  wrap.appendChild(document.createTextNode(label));
  return wrap;
}

// ── Photo ─────────────────────────────────────────────────────────────────
function handleFileChange(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const result = reader.result;
    setAttachment({ b64: result.split(",")[1], mediaType: file.type, preview: result });
    document.getElementById("preview-img").src = result;
    document.getElementById("preview-bar").classList.remove("hidden");
    document.getElementById("msg-input").placeholder = "Ajoute un mot…";
    updateSendBtn();
  };
  reader.readAsDataURL(file);
  e.target.value = "";
}

function removeAttachment() {
  setAttachment(null);
  document.getElementById("preview-bar").classList.add("hidden");
  document.getElementById("preview-img").src = "";
  document.getElementById("msg-input").placeholder = "Écris un mot…";
  updateSendBtn();
}

// ── Mic (Web Speech API) ──────────────────────────────────────────────────
function toggleMic() { _toggleMic("mic-btn", "msg-input", updateSendBtn); }
function toggleChatMic() { _toggleMic("chat-mic-btn", "chat-input", updateChatSendBtn); }
function _toggleMic(btnId, inputId, updateBtn) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { showToast("Micro non supporté ici (HTTPS requis)"); return; }
  if (recognition) {
    recognition.stop(); recognition = null;
    document.getElementById(btnId).classList.remove("recording");
    return;
  }
  const r = new SR();
  r.lang = "fr-FR"; r.continuous = false; r.interimResults = false;
  recognition = r;
  document.getElementById(btnId).classList.add("recording");
  r.onresult = e => {
    const t = e.results[0][0].transcript;
    const input = document.getElementById(inputId);
    input.value = input.value ? `${input.value} ${t}` : t;
    autoResize(input); updateBtn();
  };
  r.onerror = () => { showToast("Erreur micro"); };
  r.onend = () => { recognition = null; document.getElementById(btnId).classList.remove("recording"); };
  r.start();
}

// ── Notifications panel ───────────────────────────────────────────────────
async function openNotifs() {
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
function closeNotifs() {
  document.getElementById("notif-overlay").classList.add("hidden");
  loadDashboard();
}

// ── Tâches (overlay) ─────────────────────────────────────────────────────
// Exporté pour dashboard.js (tasksCard) — déménage dans overlays.js au step 05.
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

function closeTasks() {
  document.getElementById("tasks-overlay").classList.add("hidden");
  // Rafraîchit la card du dashboard (count + première tâche peuvent avoir bougé).
  loadDashboard();
}

// ── Météo (overlay) ──────────────────────────────────────────────────────
// Exporté pour dashboard.js (weatherCard) — déménage dans overlays.js au step 05.
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

function closeWeather() {
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
// Exporté pour dashboard.js (eventCard) — déménage dans overlays.js au step 05.
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

function closeEvents() {
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

// ── Mode chat (overlay) ───────────────────────────────────────────────────
function openChat() {
  document.getElementById("chat-view").classList.remove("hidden");
  renderChatFeed();
  setTimeout(() => document.getElementById("chat-input").focus(), 50);
}
function closeChat() {
  document.getElementById("chat-view").classList.add("hidden");
}
function renderChatFeed() {
  const feed = document.getElementById("chat-feed");
  feed.innerHTML = "";
  if (chatHistory.length === 0) {
    const row = el("div", "row bot");
    const avatar = el("div", "avatar-sm");
    avatar.appendChild(lucideNode("bot", 16));
    row.appendChild(avatar);
    const bubble = el("div", "bubble bot");
    bubble.appendChild(el("span", "bubble-text", "On peut discuter ici sans que ça pollue ton dashboard. Vas-y."));
    row.appendChild(bubble);
    feed.appendChild(row);
    return;
  }
  chatHistory.forEach(m => feed.appendChild(makeChatRow(m.role, m.text, m.imgSrc, m.error)));
  feed.scrollTop = feed.scrollHeight;
}
function makeChatRow(role, text, imgSrc, error) {
  const row = el("div", `row ${role === "user" ? "user" : "bot"}`);
  if (role !== "user") {
    const avatar = el("div", "avatar-sm");
    avatar.appendChild(lucideNode("bot", 16));
    row.appendChild(avatar);
  }
  const bubble = el("div", `bubble ${role === "user" ? "user" : "bot"}${error ? " error" : ""}`);
  if (imgSrc) {
    const img = document.createElement("img");
    img.src = imgSrc;
    img.className = "bubble-img";
    img.alt = "photo";
    bubble.appendChild(img);
  }
  if (text) {
    const span = el("span", "bubble-text");
    if (error) {
      span.appendChild(lucideNode("alert-triangle", 14, "lucide-warn"));
      span.appendChild(document.createTextNode(" " + text));
    } else if (role === "user") {
      span.appendChild(document.createTextNode(text));
    } else {
      // Réponse du bot : markdown rendu (échappé HTML dans renderMarkdown).
      span.classList.add("chat-md");
      span.innerHTML = renderMarkdown(text);
    }
    bubble.appendChild(span);
  }
  row.appendChild(bubble);
  return row;
}

function handleChatFileChange(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const result = reader.result;
    setChatAttachment({ b64: result.split(",")[1], mediaType: file.type, preview: result });
    document.getElementById("chat-preview-img").src = result;
    document.getElementById("chat-preview-bar").classList.remove("hidden");
    document.getElementById("chat-input").placeholder = "Ajoute un mot…";
    updateChatSendBtn();
  };
  reader.readAsDataURL(file);
  e.target.value = "";
}

function removeChatAttachment() {
  setChatAttachment(null);
  document.getElementById("chat-preview-bar").classList.add("hidden");
  document.getElementById("chat-preview-img").src = "";
  document.getElementById("chat-input").placeholder = "Écris…";
  updateChatSendBtn();
}

async function chatSend() {
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if ((!text && !chatAttachment) || loading) return;
  setLoading(true, "chat");

  // Snapshot la pièce jointe avant de reset, pour pouvoir l'afficher
  // dans la bulle utilisateur et la transmettre à callImage.
  const att = chatAttachment;
  chatHistory.push({ role: "user", text, imgSrc: att?.preview ?? null });
  removeChatAttachment();
  renderChatFeed();
  input.value = ""; autoResize(input); updateChatSendBtn();

  // Indicateur "en train d'écrire"
  const feed = document.getElementById("chat-feed");
  const typing = el("div", "row bot typing-row");
  typing.id = "chat-typing";
  const typingAvatar = el("div", "avatar-sm");
  typingAvatar.appendChild(lucideNode("bot", 16));
  typing.appendChild(typingAvatar);
  const tb = el("div", "bubble bot typing");
  tb.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
  typing.appendChild(tb); feed.appendChild(typing); feed.scrollTop = feed.scrollHeight;

  // Photos : pas de streaming (multimodal via /ask/image, réponse en un bloc).
  if (att) {
    try {
      const body = await callImage(text, att);
      chatHistory.push({ role: "assistant", text: body.response });
      if (body.refresh_cards && body.refresh_cards.length > 0) loadDashboard();
    } catch (e) {
      chatHistory.push({ role: "assistant", text: "Impossible de joindre Copain.", error: true });
    } finally {
      setLoading(false, "chat");
      document.getElementById("chat-typing")?.remove();
      renderChatFeed();
    }
    return;
  }

  // Texte : streaming SSE. Une bulle "live" remplace l'indicateur typing au
  // premier delta et est re-rendue en markdown à chaque chunk ; à la fin,
  // renderChatFeed() reconstruit le feed proprement depuis chatHistory.
  let acc = "";
  let liveSpan = null;
  const renderLive = () => {
    if (!liveSpan) {
      document.getElementById("chat-typing")?.remove();
      const row = el("div", "row bot");
      row.id = "chat-live";
      const avatar = el("div", "avatar-sm");
      avatar.appendChild(lucideNode("bot", 16));
      row.appendChild(avatar);
      const bubble = el("div", "bubble bot");
      liveSpan = el("span", "bubble-text chat-md");
      bubble.appendChild(liveSpan);
      row.appendChild(bubble);
      feed.appendChild(row);
    }
    liveSpan.innerHTML = renderMarkdown(acc);
    feed.scrollTop = feed.scrollHeight;
  };

  let streamError = null;
  try {
    await callTextStream(text, {
      onDelta(t) { acc += t; renderLive(); },
      onReplace(t) { acc = t; renderLive(); },
      onDone(intent, refreshCards) {
        if (refreshCards && refreshCards.length > 0) loadDashboard();
      },
      onError(t) { streamError = t || "Impossible de joindre Copain."; }
    });
    if (streamError) {
      chatHistory.push({ role: "assistant", text: streamError, error: true });
    } else {
      chatHistory.push({ role: "assistant", text: acc });
    }
  } catch (e) {
    chatHistory.push({ role: "assistant", text: "Impossible de joindre Copain.", error: true });
  } finally {
    setLoading(false, "chat");
    document.getElementById("chat-typing")?.remove();
    document.getElementById("chat-live")?.remove();
    renderChatFeed();
  }
}

// ── Composer / chat (déménage dans composer.js et chat.js au step 05) ────
function setLoading(val, scope) {
  setLoadingFlag(val);
  if (scope === "chat") {
    document.getElementById("chat-send-btn").disabled = val || !canChatSend();
  } else {
    document.getElementById("send-btn").disabled = val || !canSend();
  }
}
function canSend() {
  return (document.getElementById("msg-input").value.trim() || !!attachment) && !loading;
}
function canChatSend() {
  return (document.getElementById("chat-input").value.trim() || !!chatAttachment) && !loading;
}
function updateSendBtn() { document.getElementById("send-btn").disabled = !canSend(); }
function updateChatSendBtn() { document.getElementById("chat-send-btn").disabled = !canChatSend(); }
function handleKey(e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }
function handleChatKey(e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); chatSend(); } }
function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 110) + "px";
}

// ── Bindings DOM ──────────────────────────────────────────────────────────
// Remplacent les attributs on* inline du HTML statique : en
// <script type="module">, les fonctions ne sont plus dans le scope global,
// les handlers inline (onclick="send()" …) ne les voient donc plus.
// Le module est différé par nature : le DOM statique est déjà parsé ici.
function bindStaticHandlers() {
  const $ = (sel) => document.querySelector(sel);
  // Ferme l'overlay uniquement au tap sur le fond (équivalent de l'ancien
  // onclick="if(event.target===this)closeX()").
  const closeOnBackdrop = (close) => (e) => { if (e.target === e.currentTarget) close(); };

  // Header
  $("#chat-btn").addEventListener("click", openChat);
  $("#bell-btn").addEventListener("click", openNotifs);

  // Bulle éphémère
  $("#ephemeral").addEventListener("click", hideEphemeral);

  // Composer du dashboard
  $("#preview-bar .remove-btn").addEventListener("click", removeAttachment);
  $("#file-input").addEventListener("change", handleFileChange);
  $('#bar .icon-btn[title="Joindre une photo"]').addEventListener("click", () => $("#file-input").click());
  $("#mic-btn").addEventListener("click", toggleMic);
  const msgInput = $("#msg-input");
  msgInput.addEventListener("keydown", handleKey);
  msgInput.addEventListener("input", () => { autoResize(msgInput); updateSendBtn(); });
  $("#send-btn").addEventListener("click", send);

  // Overlays (tap sur le fond + bouton croix)
  $("#notif-overlay").addEventListener("click", closeOnBackdrop(closeNotifs));
  $("#notif-overlay .close-btn").addEventListener("click", closeNotifs);
  $("#tasks-overlay").addEventListener("click", closeOnBackdrop(closeTasks));
  $("#tasks-overlay .close-btn").addEventListener("click", closeTasks);
  $("#weather-overlay").addEventListener("click", closeOnBackdrop(closeWeather));
  $("#weather-overlay .close-btn").addEventListener("click", closeWeather);
  $("#events-overlay").addEventListener("click", closeOnBackdrop(closeEvents));
  $("#events-overlay .close-btn").addEventListener("click", closeEvents);

  // Vue markdown
  $("#markdown-view header .header-btn").addEventListener("click", closeMarkdownView);

  // Mode chat
  $("#chat-view header .header-btn").addEventListener("click", closeChat);
  $("#chat-preview-bar .remove-btn").addEventListener("click", removeChatAttachment);
  $("#chat-file-input").addEventListener("change", handleChatFileChange);
  $('#chat-bar .icon-btn[title="Joindre une photo"]').addEventListener("click", () => $("#chat-file-input").click());
  $("#chat-mic-btn").addEventListener("click", toggleChatMic);
  const chatInput = $("#chat-input");
  chatInput.addEventListener("keydown", handleChatKey);
  chatInput.addEventListener("input", () => { autoResize(chatInput); updateChatSendBtn(); });
  $("#chat-send-btn").addEventListener("click", chatSend);
}
bindStaticHandlers();
