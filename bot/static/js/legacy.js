// ── Config ────────────────────────────────────────────────────────────────
let API_KEY = "";
const API_BASE = "";
const PROFILE_NAME = "Arnaud";

// ── État ──────────────────────────────────────────────────────────────────
let loading      = false;
let attachment   = null;   // { b64, mediaType, preview }
let recognition  = null;
let toastTimer   = null;
let ephemeralTimer = null;
let dashboardData = null;
let chatHistory  = [];     // [{role, text}]
// Card Actu : état persistant en mémoire (la card reste « fraîche » tant
// que la PWA est ouverte ; un reload de la page remet à zéro).
let newsState   = { fetchedAt: null, loading: false, markdown: null };

// ── Init ──────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  setupAppHeight();
  renderGreeting();
  try {
    const cfg = await fetch("/config").then(r => r.json());
    API_KEY = cfg.api_key;
  } catch (e) {
    // /config est sur le même réseau Tailscale ; un échec ici est critique.
    showToast("Configuration indisponible");
  }
  await loadDashboard();
  // Refresh périodique léger pour le count notif et la météo (toutes les 2 min).
  setInterval(loadDashboard, 120_000);
});

// Pilote --app-h depuis window.visualViewport.height (fallback innerHeight).
// Sur iOS 26 PWA standalone, 100dvh peut renvoyer une valeur qui ne reflète
// pas la viewport visuelle réelle, ce qui laissait apparaître une bande
// vide sous la composer bar. visualViewport remonte la vraie hauteur
// utilisable (et s'ajuste quand le clavier s'ouvre/se ferme).
function setupAppHeight() {
  // Mesure unique au boot : la viewport disponible (= sans clavier).
  // On ne ré-écoute PAS les resize / visualViewport.resize : sur iOS,
  // ces events se déclenchent aussi à l'ouverture du clavier, ce qui
  // ferait chuter --app-h et écraserait le #app en haut de l'écran.
  // En gardant la valeur initiale, iOS translate automatiquement le
  // visual viewport pour amener l'input au-dessus du clavier.
  const measure = () => {
    const h = window.visualViewport?.height || window.innerHeight;
    document.documentElement.style.setProperty("--app-h", `${h}px`);
  };
  measure();
  // Seul l'orientationchange justifie une nouvelle mesure : c'est un
  // vrai changement de viewport, indépendant du clavier.
  window.addEventListener("orientationchange", () => {
    // Petit délai pour laisser iOS finir sa rotation avant de mesurer.
    setTimeout(measure, 150);
  });
}

function renderGreeting() {
  document.getElementById("greeting-name").textContent = `Bonjour ${PROFILE_NAME}`;
  const d = new Date();
  const opts = { weekday: "long", day: "numeric", month: "long" };
  document.getElementById("greeting-date").textContent = d.toLocaleDateString("fr-FR", opts);
}

// ── Dashboard ─────────────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const res = await fetch(`${API_BASE}/dashboard`, { headers: { "X-API-Key": API_KEY } });
    if (!res.ok) throw new Error(`${res.status}`);
    dashboardData = await res.json();
    renderDashboard(dashboardData);
    renderBellBadge(dashboardData.unread_notifications);
  } catch (e) {
    renderDashboardError();
  }
}

function renderDashboard(d) {
  const root = document.getElementById("dashboard");
  root.innerHTML = "";

  // Météo
  root.appendChild(weatherCard(d.weather));
  // Prochain évent
  root.appendChild(eventCard(d.next_event));
  // Tâches
  root.appendChild(tasksCard(d.today_tasks, d.overdue_tasks || 0));
  // Budget (restant ce mois)
  root.appendChild(budgetCard(d.budget));
  // Actu (vraie card avec fetch au clic)
  root.appendChild(newsCard());
}

function renderDashboardError() {
  const root = document.getElementById("dashboard");
  root.innerHTML = `
    <div class="card empty">
      <div class="card-primary">Dashboard indisponible</div>
      <div class="card-secondary">Vérifie que le Pi est accessible via Tailscale, puis tire pour rafraîchir.</div>
    </div>`;
}

function weatherCard(w) {
  const card = el("div", "card");
  if (!w) {
    card.classList.add("empty");
    card.appendChild(makeHead("cloud-sun", "Météo"));
    card.appendChild(el("div", "card-primary", "Indisponible"));
    return card;
  }
  card.classList.add("tappable");
  card.onclick = openWeather;
  card.appendChild(makeHead("cloud-sun", "Météo · " + w.city));
  const tempLine = `${Math.round(w.temp_current)}° · ${w.description}`;
  card.appendChild(el("div", "card-primary", tempLine));
  const detail = `min ${Math.round(w.temp_min)}° / max ${Math.round(w.temp_max)}°`
    + (w.precipitation_mm > 0 ? ` · ${w.precipitation_mm.toFixed(1)} mm` : "");
  card.appendChild(el("div", "card-secondary", detail));
  return card;
}

function eventCard(e) {
  const card = el("div", "card");
  if (!e) {
    card.classList.add("empty");
    card.appendChild(makeHead("calendar", "Prochain évènement"));
    card.appendChild(el("div", "card-primary", "Rien à venir"));
    card.classList.add("tappable");
    card.onclick = openEvents;
    return card;
  }
  card.classList.add("tappable");
  card.onclick = openEvents;
  card.appendChild(makeHead("calendar", "Prochain évènement · " + e.calendar_name));
  const start = new Date(e.start);
  const end = new Date(e.end);
  const allDay = isAllDayEvent(start, end);
  const dayWord = sameDay(start, new Date()) ? "Aujourd'hui" : formatRelativeDay(start);
  const dayLabel = allDay ? dayWord : `${dayWord} ${formatHM(start)}`;
  card.appendChild(el("div", "card-primary", `${dayLabel} — ${e.title}`));
  if (e.location) card.appendChild(el("div", "card-secondary", e.location));
  return card;
}

function tasksCard(tasks, overdueCount) {
  const card = el("div", "card tappable");
  card.onclick = openTasks;
  if (!tasks || tasks.length === 0) {
    card.classList.add("empty");
    card.appendChild(makeHead("list-checks", "Tâches du jour"));
    card.appendChild(el("div", "card-primary", "Aucune tâche aujourd'hui"));
    card.appendChild(el("div", "card-secondary", "Tape pour voir toutes les tâches"));
    appendOverdueLine(card, overdueCount);
    return card;
  }
  const label = tasks.length === 1 ? "1 tâche aujourd'hui" : `${tasks.length} tâches aujourd'hui`;
  card.appendChild(makeHead("list-checks", label));
  // Première tâche en focus
  const t = tasks[0];
  const suffix = t.due_at ? ` · ${formatHM(new Date(t.due_at))}` : "";
  card.appendChild(el("div", "card-primary", t.content + suffix));
  if (tasks.length > 1) {
    card.appendChild(el("div", "card-meta", `+ ${tasks.length - 1} autre${tasks.length > 2 ? "s" : ""}`));
  } else {
    card.appendChild(el("div", "card-meta", "Tape pour gérer"));
  }
  appendOverdueLine(card, overdueCount);
  return card;
}

function appendOverdueLine(card, overdueCount) {
  if (!overdueCount || overdueCount <= 0) return;
  const line = el(
    "div",
    "card-meta",
    overdueCount === 1 ? "1 en retard" : `${overdueCount} en retard`
  );
  line.style.color = "var(--red)";
  card.appendChild(line);
}

function budgetCard(b) {
  const card = el("div", "card");
  card.appendChild(makeHead("wallet", "Budget"));
  if (!b) {
    card.classList.add("empty");
    card.appendChild(el("div", "card-primary", "Non configuré"));
    card.appendChild(el("div", "card-secondary", "Ajoute la section `finances` dans data/profile.yaml"));
    return card;
  }
  card.classList.add("tappable");
  card.onclick = openBudget;
  const remaining = formatEur(b.remaining_eur);
  const primary = el("div", "card-primary", `Restant : ${remaining}`);
  if (b.remaining_eur < 0) primary.style.color = "var(--red)";
  card.appendChild(primary);
  const savedLine = `Épargné cette année : ${formatEur(b.saved_this_year_eur)}`;
  card.appendChild(el("div", "card-secondary", savedLine));
  if (b.pending_recurring_count > 0) {
    const meta = el(
      "div",
      "card-meta",
      b.pending_recurring_count === 1
        ? "1 récurrente à pointer"
        : `${b.pending_recurring_count} récurrentes à pointer`
    );
    if (b.has_overdue) meta.style.color = "var(--red)";
    card.appendChild(meta);
  }
  if (b.envelopes && b.envelopes.length > 0) {
    const wrap = el("div", "budget-envelopes");
    wrap.style.marginTop = "8px";
    for (const env of b.envelopes) {
      wrap.appendChild(envelopeRow(env));
    }
    card.appendChild(wrap);
  }
  return card;
}

function envelopeRow(env) {
  const row = el("div", "envelope-row" + (env.shared ? " shared" : ""));
  row.style.fontSize = "0.85em";
  row.style.marginTop = "4px";

  const top = el("div", "envelope-line");
  top.style.display = "flex";
  top.style.justifyContent = "space-between";
  const labelWrap = el("span", "envelope-label-wrap");
  const label = el("span", "envelope-label", env.label);
  labelWrap.appendChild(label);
  if (env.shared) {
    labelWrap.appendChild(el("span", "envelope-shared-badge", "compte joint"));
  }
  const amounts = el(
    "span",
    "envelope-amounts",
    `${formatEur(env.spent_eur)} / ${formatEur(env.allocated_eur)}`
  );
  if (env.is_overrun) amounts.classList.add("envelope-amount-overrun");
  top.appendChild(labelWrap);
  top.appendChild(amounts);
  row.appendChild(top);

  // Progress bar : bg gris (var --track-bg, adapté light/dark), fill jusqu'au
  // % consommé (capé à 100). En cas de dépassement, on remplit à 100% en rouge.
  // Une enveloppe shared est grisée pour rappeler qu'elle est hors gestion perso.
  const ratio = env.allocated_eur > 0
    ? Math.min(1, env.spent_eur / env.allocated_eur)
    : 0;
  const track = el("div", "envelope-track");
  const fillClass = env.is_overrun
    ? "envelope-fill overrun"
    : env.shared
      ? "envelope-fill shared"
      : "envelope-fill";
  const fill = el("div", fillClass);
  fill.style.width = `${ratio * 100}%`;
  track.appendChild(fill);
  row.appendChild(track);
  return row;
}

function formatEur(amount) {
  const rounded = Math.round(amount * 100) / 100;
  if (Number.isInteger(rounded)) return `${rounded} €`;
  return `${rounded.toFixed(2).replace(".", ",")} €`;
}

async function openBudget() {
  try {
    const res = await fetch(`${API_BASE}/budget`, { headers: { "X-API-Key": API_KEY } });
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();
    openMarkdownView(
      "Budget du cycle",
      renderBudgetMarkdown(data),
      null,
      {
        label: "Exporter CSV",
        onClick: () => exportExpensesCsv(data.cycle_start || data.month, data.cycle_end),
      }
    );
  } catch (e) {
    showToast("Impossible de charger le budget");
  }
}

async function exportExpensesCsv(startIso, endIso) {
  // Bornes du cycle budgétaire courant (début = jour du salaire, fin =
  // veille du prochain salaire ou aujourd'hui si le cycle est ouvert).
  const start = startIso;
  let end = endIso;
  if (!end) {
    // Fallback (réponse sans cycle_end) : fin du mois civil du début.
    const startDate = new Date(start + "T00:00:00");
    const lastDay = new Date(startDate.getFullYear(), startDate.getMonth() + 1, 0);
    const yyyy = lastDay.getFullYear();
    const mm = String(lastDay.getMonth() + 1).padStart(2, "0");
    const dd = String(lastDay.getDate()).padStart(2, "0");
    end = `${yyyy}-${mm}-${dd}`;
  }
  try {
    const res = await fetch(
      `${API_BASE}/expenses/export.csv?from=${start}&to=${end}`,
      { headers: { "X-API-Key": API_KEY } }
    );
    if (!res.ok) throw new Error(`${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `copain-depenses-${start}_${end}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast("Export CSV téléchargé");
  } catch (e) {
    showToast("Export CSV impossible");
  }
}

function renderBudgetMarkdown(b) {
  const lines = [];
  const startIso = b.cycle_start || b.month;
  const fmtShort = (iso) =>
    new Date(iso + "T00:00:00").toLocaleDateString("fr-FR", { day: "numeric", month: "long" });
  if (b.cycle_end) {
    lines.push(`## Cycle du ${fmtShort(startIso)} au ${fmtShort(b.cycle_end)}`);
  } else {
    const monthLabel = new Date(startIso + "T00:00:00").toLocaleDateString("fr-FR", {
      month: "long",
      year: "numeric",
    });
    lines.push(`## ${monthLabel.charAt(0).toUpperCase() + monthLabel.slice(1)}`);
  }
  lines.push("");
  lines.push(`**Restant prévisionnel : ${formatEur(b.remaining_eur)}**`);
  lines.push("");
  lines.push(`- Revenu : ${formatEur(b.income_eur)}`);
  lines.push(`- Récurrentes pointées : ${formatEur(b.spent_recurring_eur)}`);
  lines.push(`- Ponctuelles : ${formatEur(b.spent_punctual_eur)}`);
  lines.push(`- Épargne ce cycle : ${formatEur(b.saved_this_month_eur)}`);
  lines.push(`- Épargné cette année : ${formatEur(b.saved_this_year_eur)}`);
  lines.push("");
  if (b.envelopes && b.envelopes.length > 0) {
    lines.push("### Enveloppes");
    for (const env of b.envelopes) {
      const overrun = env.is_overrun
        ? ` {{lucide:alert-triangle}} dépassement de ${formatEur(env.overrun_eur)}`
        : "";
      const sharedTag = env.shared ? " _(compte joint)_" : "";
      lines.push(
        `- **${env.label}**${sharedTag} : ${formatEur(env.spent_eur)} / ${formatEur(env.allocated_eur)}${overrun}`
      );
    }
    lines.push("");
  }
  if (b.pending && b.pending.length > 0) {
    lines.push(`### À pointer (${b.pending.length})`);
    for (const p of b.pending) {
      const overdue = p.is_overdue ? " {{lucide:alert-triangle}} en retard" : "";
      const kind = p.kind === "saving" ? " (épargne)" : "";
      lines.push(`- **${p.label}** ${formatEur(p.amount_eur)}, prévu le ${p.day}${kind}${overdue}`);
    }
    lines.push("");
  }
  if (b.transactions && b.transactions.length > 0) {
    lines.push("### Transactions du mois");
    for (const t of b.transactions) {
      const d = new Date(t.occurred_on + "T00:00:00");
      const dayStr = d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
      const sign = t.kind === "income" ? "+" : "−";
      const cat = t.category ? ` (${t.category})` : "";
      const sharedTag = t.shared ? " _(compte joint)_" : "";
      lines.push(`- ${dayStr} — ${t.label}${cat}${sharedTag} : ${sign}${formatEur(t.amount_eur)}`);
    }
  } else {
    lines.push("_Aucune transaction enregistrée ce mois._");
  }
  return lines.join("\n");
}

function newsCard() {
  const card = el("div", "card tappable");
  card.onclick = openNews;
  card.appendChild(makeHead("newspaper", "Actu"));
  if (newsState.loading) {
    card.appendChild(el("div", "card-primary", "Chargement…"));
    card.appendChild(el("div", "card-meta", "Curation des dernières 24h"));
    return card;
  }
  if (newsState.markdown) {
    card.appendChild(el("div", "card-primary", "Tape pour relire"));
    const ago = newsState.fetchedAt ? formatRelativeAge(newsState.fetchedAt) : "";
    card.appendChild(el("div", "card-meta", ago ? `Mis à jour ${ago}` : ""));
    return card;
  }
  card.classList.add("empty");
  card.appendChild(el("div", "card-primary", "Tape pour les dernières actus"));
  card.appendChild(el("div", "card-meta", "Curation IA des 24h"));
  return card;
}

async function openNews() {
  // Si on a déjà un fetch en mémoire, on rouvre l'overlay sans refetch.
  if (newsState.markdown && !newsState.loading) {
    openMarkdownView("Actu du jour", newsState.markdown, newsState.fetchedAt);
    return;
  }
  if (newsState.loading) return;

  newsState.loading = true;
  if (dashboardData) renderDashboard(dashboardData);

  try {
    const res = await fetch(`${API_BASE}/news/latest`, {
      headers: { "X-API-Key": API_KEY }
    });
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();
    newsState.markdown = data.markdown || "";
    newsState.fetchedAt = data.fetched_at;
    newsState.loading = false;
    if (dashboardData) renderDashboard(dashboardData);
    openMarkdownView("Actu du jour", newsState.markdown, newsState.fetchedAt);
  } catch (e) {
    newsState.loading = false;
    if (dashboardData) renderDashboard(dashboardData);
    showToast("Impossible de charger les actus");
  }
}

function formatRelativeAge(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const ageMin = Math.max(0, Math.floor((Date.now() - d.getTime()) / 60_000));
  if (ageMin < 1) return "à l'instant";
  if (ageMin < 60) return `il y a ${ageMin} min`;
  const ageH = Math.floor(ageMin / 60);
  if (ageH < 24) return `il y a ${ageH} h`;
  return `il y a ${Math.floor(ageH / 24)} j`;
}

function renderBellBadge(count) {
  const btn = document.getElementById("bell-btn");
  btn.querySelector(".badge")?.remove();
  if (count > 0) {
    const badge = el("span", "badge", count > 9 ? "9+" : String(count));
    btn.appendChild(badge);
  }
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

function flashCards(names) {
  // On flash visuellement les cards concernées pour que l'œil les repère.
  document.querySelectorAll("#dashboard .card").forEach(c => c.classList.remove("flash"));
  // Pas de mapping précis name→DOM (le dashboard est rendu fraîchement) ;
  // on flash juste TOUTES les cards une fois pour signaler le rafraîchissement.
  // Une version v2 ferait un mapping par data-attr si besoin.
  setTimeout(() => {
    document.querySelectorAll("#dashboard .card").forEach((c, i) => {
      if (names.length > 0 && i < 3) c.classList.add("flash");
    });
  }, 30);
}

async function callText(message) {
  const res = await fetch(`${API_BASE}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify({ message })
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return await res.json();
}

async function callImage(message, att) {
  const res = await fetch(`${API_BASE}/ask/image`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify({ message: message || "", image_b64: att.b64, media_type: att.mediaType })
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return await res.json();
}

/**
 * Appel streamé de /ask/stream (SSE sur POST via fetch + ReadableStream).
 * `handlers` : { onDelta(text), onReplace(text), onDone(intent, refreshCards), onError(text) }.
 * Les frames sont de la forme `data: {json}\n\n` (cf. bot/api.py).
 */
async function callTextStream(message, handlers) {
  const res = await fetch(`${API_BASE}/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify({ message })
  });
  if (!res.ok || !res.body) throw new Error(`${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const line = frame.split("\n").find(l => l.startsWith("data: "));
      if (!line) continue;
      let evt;
      try { evt = JSON.parse(line.slice(6)); } catch { continue; }
      if (evt.type === "delta") handlers.onDelta(evt.text || "");
      else if (evt.type === "replace") handlers.onReplace(evt.text || "");
      else if (evt.type === "done") handlers.onDone(evt.intent, evt.refresh_cards || []);
      else if (evt.type === "error") handlers.onError(evt.text || "");
    }
  }
}

// ── Bulle éphémère ────────────────────────────────────────────────────────
function showEphemeral(text, isError) {
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
function hideEphemeral() {
  document.getElementById("ephemeral").classList.add("hidden");
  clearTimeout(ephemeralTimer);
}

// ── Toast ─────────────────────────────────────────────────────────────────
function showToast(msg) {
  document.getElementById("toast")?.remove();
  clearTimeout(toastTimer);
  const t = el("div", "", msg);
  t.id = "toast";
  document.getElementById("app").appendChild(t);
  toastTimer = setTimeout(() => t.remove(), 1800);
}

// ── Photo ─────────────────────────────────────────────────────────────────
function handleFileChange(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const result = reader.result;
    attachment = { b64: result.split(",")[1], mediaType: file.type, preview: result };
    document.getElementById("preview-img").src = result;
    document.getElementById("preview-bar").classList.remove("hidden");
    document.getElementById("msg-input").placeholder = "Ajoute un mot…";
    updateSendBtn();
  };
  reader.readAsDataURL(file);
  e.target.value = "";
}

function removeAttachment() {
  attachment = null;
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
async function openTasks() {
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
async function openWeather() {
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
async function openEvents() {
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

function isAllDayEvent(start, end) {
  // All-day iCloud : DTSTART/DTEND alignés sur minuit local, durée
  // multiple de 24 h (cas d'un jour : 24 h ; cas multi-jours : 48 h, …).
  if (start.getHours() !== 0 || start.getMinutes() !== 0 || start.getSeconds() !== 0) return false;
  if (end.getHours() !== 0 || end.getMinutes() !== 0 || end.getSeconds() !== 0) return false;
  const diffMs = end.getTime() - start.getTime();
  return diffMs > 0 && diffMs % 86400000 === 0;
}

// ── Markdown view (réutilisé pour la card Actu) ────────────────────────
function openMarkdownView(title, text, subtitle, action) {
  const view = document.getElementById("markdown-view");
  const body = document.getElementById("markdown-body");
  const titleEl = document.getElementById("markdown-title");
  const subtitleEl = document.getElementById("markdown-subtitle");
  const actionBtn = document.getElementById("markdown-action-btn");
  titleEl.textContent = title || "";
  if (subtitle) {
    // Accepte soit un timestamp ISO, soit un texte libre.
    const d = new Date(subtitle);
    subtitleEl.textContent = isNaN(d.getTime())
      ? subtitle
      : d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
  } else {
    subtitleEl.textContent = "";
  }
  // Bouton d'action contextuel (ex: "Exporter CSV" depuis la card Budget).
  if (action && action.label && typeof action.onClick === "function") {
    actionBtn.textContent = action.label;
    actionBtn.onclick = action.onClick;
    actionBtn.classList.remove("hidden");
  } else {
    actionBtn.textContent = "";
    actionBtn.onclick = null;
    actionBtn.classList.add("hidden");
  }
  body.innerHTML = renderMarkdown(text || "");
  view.classList.remove("hidden");
  body.scrollTop = 0;
}

function closeMarkdownView() {
  document.getElementById("markdown-view").classList.add("hidden");
}

/**
 * Mini-parseur markdown utilisé par la card Actu (et plus largement
 * tout texte enrichi côté backend).
 *
 * Couvre :
 *  - `**gras**`            → <strong>
 *  - `*italique*`          → <em>
 *  - URL nue               → <a href>
 *  - lignes commençant par `- ` → groupées dans une <ul>
 *  - lignes commençant par un emoji + `*titre*` → <h3>
 *  - autres lignes non vides → <p>
 *
 * On échappe le HTML avant traitement pour éviter toute injection
 * via le contenu (URLs externes, titres d'articles, etc.).
 */
function renderMarkdown(text) {
  const esc = escHtml(text);
  const lines = esc.split("\n");
  const out = [];
  let listBuffer = [];

  const flushList = () => {
    if (listBuffer.length === 0) return;
    out.push("<ul>" + listBuffer.map(l => `<li>${l}</li>`).join("") + "</ul>");
    listBuffer = [];
  };

  for (const raw of lines) {
    const line = inlineMd(raw);
    const stripped = raw.trim();
    if (!stripped) { flushList(); continue; }

    // Titres ATX : "# ", "## ", "### "
    const h = stripped.match(/^(#{1,3})\s+(.+)$/);
    if (h) {
      flushList();
      const level = h[1].length;
      out.push(`<h${level}>${inlineMd(h[2])}</h${level}>`);
      continue;
    }

    // Titres de section : "📰 *Actus du jour*" ou "🤖 *Actus IA …*"
    if (/^(📰|🤖|☀️|📅|✅|📋|🌤|🌧)/.test(stripped) && /\*[^*]+\*/.test(stripped)) {
      flushList();
      out.push(`<h3>${inlineMd(raw)}</h3>`);
      continue;
    }

    // Item de liste : commence par "- " ou "* " (puces LLM)
    if (/^[-*]\s+/.test(stripped)) {
      listBuffer.push(inlineMd(raw.replace(/^\s*[-*]\s+/, "")));
      continue;
    }

    // Ligne d'indentation (continuation d'un item, ex: URL sur ligne suivante)
    if (/^\s{2,}/.test(raw) && listBuffer.length > 0) {
      listBuffer[listBuffer.length - 1] += `<br>${inlineMd(raw.trim())}`;
      continue;
    }

    flushList();
    out.push(`<p>${line}</p>`);
  }
  flushList();
  // Expansion des placeholders d'icônes ({{lucide:name}}) après rendu.
  return out.join("").replace(/\{\{lucide:([a-z-]+)\}\}/g, (_m, name) => lucideSvg(name, 14, "lucide-warn"));
}

function inlineMd(s) {
  return s
    // **gras** (greedy minimal)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    // *italique* (sans capturer **)
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>")
    // URLs nues → liens (target=_blank pour ouvrir hors PWA)
    .replace(
      /(https?:\/\/[^\s<>"'()]+)/g,
      '<a href="$1" target="_blank" rel="noopener">$1</a>'
    );
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

// Pièce jointe spécifique au mode chat (séparée de `attachment` utilisée
// par la barre principale, pour que les deux vues ne s'écrasent pas).
let chatAttachment = null;

function handleChatFileChange(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const result = reader.result;
    chatAttachment = { b64: result.split(",")[1], mediaType: file.type, preview: result };
    document.getElementById("chat-preview-img").src = result;
    document.getElementById("chat-preview-bar").classList.remove("hidden");
    document.getElementById("chat-input").placeholder = "Ajoute un mot…";
    updateChatSendBtn();
  };
  reader.readAsDataURL(file);
  e.target.value = "";
}

function removeChatAttachment() {
  chatAttachment = null;
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

// ── UI helpers ────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function el(tag, cls, content) {
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
function lucideSvg(name, size, extraClass) {
  const inner = LUCIDE_ICONS[name];
  if (!inner) return "";
  const cls = "lucide lucide-" + name + (extraClass ? " " + extraClass : "");
  const dim = size ? ` width="${size}" height="${size}"` : "";
  return `<svg class="${cls}"${dim} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
}
// Retourne un nœud DOM pour appendChild direct.
function lucideNode(name, size, extraClass) {
  const wrap = document.createElement("span");
  wrap.innerHTML = lucideSvg(name, size, extraClass);
  return wrap.firstChild;
}
function makeHead(iconName, label) {
  const head = el("div", "card-head");
  const iconWrap = el("div", "card-icon");
  iconWrap.appendChild(lucideNode(iconName, 16));
  head.appendChild(iconWrap);
  head.appendChild(el("div", "card-label", label));
  return head;
}
function setLoading(val, scope) {
  loading = val;
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

// ── Date helpers ──────────────────────────────────────────────────────────
function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}
function formatHM(d) {
  return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}
function formatRelativeDay(d) {
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  if (sameDay(d, tomorrow)) return "Demain";
  return d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" });
}
function formatDateTime(d) {
  return d.toLocaleString("fr-FR", { weekday: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
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
