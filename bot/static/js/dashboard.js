// ── Dashboard : chargement, renderers de cards, budget, actu ──────────────
import {
  API_KEY, API_BASE,
  dashboardData, setDashboardData,
  newsState, foryouState,
} from "./state.js";
import {
  el, makeHead,
  sameDay, formatHM, formatRelativeDay, formatRelativeAge, isAllDayEvent,
  showToast,
} from "./ui.js";
import { openMarkdownView, renderMarkdown } from "./markdown.js";
// Cycle d'import dashboard ↔ overlays accepté (cf. PROGRESS.md) : les cards
// tappables ouvrent les overlays, et les overlays rafraîchissent le
// dashboard à la fermeture. Bénin : fonctions hoistées, appelées au runtime.
import { openWeather, openEvents, openTasks, openForYou } from "./overlays.js";

// ── Dashboard ─────────────────────────────────────────────────────────────
export async function loadDashboard() {
  try {
    const res = await fetch(`${API_BASE}/dashboard`, { headers: { "X-API-Key": API_KEY } });
    if (!res.ok) throw new Error(`${res.status}`);
    setDashboardData(await res.json());
    renderDashboard(dashboardData);
    renderBellBadge(dashboardData.unread_notifications);
  } catch (e) {
    renderDashboardError();
  }
}

function renderDashboard(d) {
  const root = document.getElementById("dashboard");
  root.innerHTML = "";

  // Rangée compacte : météo + prochain évent (deux tuiles côte à côte)
  root.appendChild(gridRow(weatherCard(d.weather, true), eventCard(d.next_event, true)));
  // Tâches (pleine largeur)
  root.appendChild(tasksCard(d.today_tasks, d.overdue_tasks || 0));
  // Budget (pleine largeur, restant ce mois)
  root.appendChild(budgetCard(d.budget));
  // Rangée compacte : actu + pour toi (fetch au tap dans les deux cas)
  root.appendChild(gridRow(newsCard(true), foryouCard(true)));
}

// Enveloppe deux cards dans une rangée grille 2 colonnes.
function gridRow(left, right) {
  const row = el("div", "card-grid");
  row.appendChild(left);
  row.appendChild(right);
  return row;
}

function renderDashboardError() {
  const root = document.getElementById("dashboard");
  root.innerHTML = `
    <div class="card empty">
      <div class="card-primary">Dashboard indisponible</div>
      <div class="card-secondary">Vérifie que le Pi est accessible via Tailscale, puis tire pour rafraîchir.</div>
    </div>`;
}

function weatherCard(w, compact) {
  const card = el("div", "card");
  if (compact) card.classList.add("compact");
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

function eventCard(e, compact) {
  const card = el("div", "card");
  if (compact) card.classList.add("compact");
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

// Overlay Budget interactif : formulaire de saisie directe (POST /expenses,
// sans LLM) en haut, récap + transactions + export CSV en dessous. Le chemin
// bot (intent=expense) reste un canal parallèle inchangé.
async function openBudget() {
  document.getElementById("budget-overlay").classList.remove("hidden");
  const list = document.getElementById("budget-list");
  list.innerHTML = '<div class="panel-empty">Chargement…</div>';
  try {
    const res = await fetch(`${API_BASE}/budget`, { headers: { "X-API-Key": API_KEY } });
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();
    renderBudgetPanel(data);
  } catch (e) {
    list.innerHTML = '<div class="panel-empty">Impossible de charger</div>';
  }
}

export function closeBudget() {
  document.getElementById("budget-overlay").classList.add("hidden");
  // Rafraîchit la card du dashboard (restant + enveloppes ont pu bouger).
  loadDashboard();
}

function renderBudgetPanel(data) {
  const list = document.getElementById("budget-list");
  list.innerHTML = "";
  list.appendChild(renderBudgetForm(data));
  list.appendChild(renderBudgetDetail(data));
}

function todayIso() {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function budgetField(labelText, input, id) {
  const wrap = el("div", "budget-field");
  if (id) wrap.id = id;
  wrap.appendChild(el("label", "budget-label", labelText));
  wrap.appendChild(input);
  return wrap;
}

function renderBudgetForm(data) {
  const form = el("form", "budget-form");

  const mode = el("select", "budget-input");
  mode.id = "bf-action";
  [["spend", "Dépense"], ["income", "Revenu"], ["tick_recurring", "Pointer une récurrente"]]
    .forEach(([v, l]) => {
      const o = el("option", null, l);
      o.value = v;
      mode.appendChild(o);
    });
  form.appendChild(budgetField("Type", mode));

  // Récurrente (mode tick) — peuplée depuis les pending du cycle.
  const recur = el("select", "budget-input");
  recur.id = "bf-recurring";
  const pending = data.pending || [];
  if (pending.length === 0) {
    const o = el("option", null, "Aucune récurrente à pointer");
    o.value = "";
    recur.appendChild(o);
  } else {
    pending.forEach((p) => {
      const o = el("option", null, `${p.label} — ${formatEur(p.amount_eur)} (le ${p.day})`);
      o.value = p.key;
      recur.appendChild(o);
    });
  }
  form.appendChild(budgetField("Récurrente", recur, "bf-recurring-field"));

  const amount = el("input", "budget-input");
  amount.id = "bf-amount";
  amount.type = "number";
  amount.step = "0.01";
  amount.min = "0";
  amount.inputMode = "decimal";
  amount.placeholder = "0,00";
  form.appendChild(budgetField("Montant (€)", amount, "bf-amount-field"));

  const label = el("input", "budget-input");
  label.id = "bf-label";
  label.type = "text";
  label.placeholder = "ex. courses";
  form.appendChild(budgetField("Libellé", label, "bf-label-field"));

  const cat = el("input", "budget-input");
  cat.id = "bf-category";
  cat.type = "text";
  cat.placeholder = "ex. alimentation";
  cat.setAttribute("list", "bf-category-list");
  const dl = el("datalist");
  dl.id = "bf-category-list";
  (data.envelopes || []).forEach((env) => {
    const o = el("option");
    o.value = env.category;
    dl.appendChild(o);
  });
  const catField = budgetField("Catégorie", cat, "bf-category-field");
  catField.appendChild(dl);
  form.appendChild(catField);

  const dateInput = el("input", "budget-input");
  dateInput.id = "bf-date";
  dateInput.type = "date";
  dateInput.value = todayIso();
  form.appendChild(budgetField("Date", dateInput));

  const sharedWrap = el("label", "budget-check");
  sharedWrap.id = "bf-shared-field";
  const shared = el("input");
  shared.id = "bf-shared";
  shared.type = "checkbox";
  sharedWrap.appendChild(shared);
  sharedWrap.appendChild(el("span", null, "Compte joint"));
  form.appendChild(sharedWrap);

  const cycleWrap = el("label", "budget-check");
  cycleWrap.id = "bf-cycle-field";
  const cycle = el("input");
  cycle.id = "bf-cycle";
  cycle.type = "checkbox";
  cycleWrap.appendChild(cycle);
  cycleWrap.appendChild(el("span", null, "C'est mon salaire (démarre un cycle)"));
  form.appendChild(cycleWrap);

  const submit = el("button", "budget-submit", "Enregistrer");
  submit.type = "submit";
  form.appendChild(submit);

  mode.addEventListener("change", () => applyBudgetFormMode(form));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submitExpense(form);
  });
  applyBudgetFormMode(form);
  return form;
}

function applyBudgetFormMode(form) {
  const action = form.querySelector("#bf-action").value;
  const show = (sel, on) => {
    const node = form.querySelector(sel);
    if (node) node.style.display = on ? "" : "none";
  };
  show("#bf-recurring-field", action === "tick_recurring");
  // Montant optionnel en mode tick (override du montant YAML), requis sinon.
  show("#bf-label-field", action !== "tick_recurring");
  show("#bf-category-field", action === "spend");
  show("#bf-shared-field", action === "spend");
  show("#bf-cycle-field", action === "income");
  const amountLabel = form.querySelector("#bf-amount-field .budget-label");
  if (amountLabel) {
    amountLabel.textContent = action === "tick_recurring" ? "Montant (€) — optionnel" : "Montant (€)";
  }
}

async function submitExpense(form) {
  const action = form.querySelector("#bf-action").value;
  const rawAmount = form.querySelector("#bf-amount").value.trim().replace(",", ".");
  const amountNum = rawAmount === "" ? null : Number(rawAmount);
  const payload = {
    action,
    amount_eur: amountNum !== null && !Number.isNaN(amountNum) ? amountNum : null,
    label: form.querySelector("#bf-label").value.trim() || null,
    category: form.querySelector("#bf-category").value.trim() || null,
    occurred_on: form.querySelector("#bf-date").value || null,
    shared: form.querySelector("#bf-shared").checked,
    recurring_key:
      action === "tick_recurring" ? form.querySelector("#bf-recurring").value || null : null,
    starts_cycle: action === "income" ? form.querySelector("#bf-cycle").checked : false,
  };

  // Garde-fous côté client (le backend reste l'autorité de validation).
  if ((action === "spend" || action === "income") && (payload.amount_eur === null || payload.amount_eur <= 0)) {
    showToast("Montant requis");
    return;
  }
  if (action === "tick_recurring" && !payload.recurring_key) {
    showToast("Aucune récurrente à pointer");
    return;
  }

  const submitBtn = form.querySelector(".budget-submit");
  submitBtn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/expenses`, {
      method: "POST",
      headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`${res.status}`);
    const result = await res.json();
    showToast(result.recorded === false ? "Déjà pointé ce cycle" : "Saisie enregistrée");
    // Recharge le budget complet : récap à jour + formulaire réinitialisé
    // (date re-défaut à aujourd'hui).
    await openBudget();
  } catch (e) {
    showToast("Enregistrement impossible");
    submitBtn.disabled = false;
  }
}

function renderBudgetDetail(data) {
  const wrap = el("div", "budget-detail");
  const body = el("div", "markdown-body budget-recap");
  body.innerHTML = renderMarkdown(renderBudgetMarkdown(data));
  wrap.appendChild(body);
  const exportBtn = el("button", "budget-export", "Exporter CSV");
  exportBtn.type = "button";
  exportBtn.onclick = () => exportExpensesCsv(data.cycle_start || data.month, data.cycle_end);
  wrap.appendChild(exportBtn);
  return wrap;
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

function newsCard(compact) {
  const card = el("div", "card tappable");
  if (compact) card.classList.add("compact");
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

function foryouCard(compact) {
  // Card volontairement neutre : aucun badge ni compteur « N choses
  // t'attendent » (ce serait une charge mentale entrante, contraire au
  // positionnement produit). Toujours le même libellé apaisant, idle/loading
  // gérés dans l'overlay au tap.
  const card = el("div", "card tappable empty");
  if (compact) card.classList.add("compact");
  card.onclick = openForYou;
  card.appendChild(makeHead("inbox", "Pour toi"));
  card.appendChild(el("div", "card-primary", "Tape pour faire le point"));
  card.appendChild(el("div", "card-meta", "Tes dépôts, remis en perspective"));
  return card;
}

export function renderBellBadge(count) {
  const btn = document.getElementById("bell-btn");
  btn.querySelector(".badge")?.remove();
  if (count > 0) {
    const badge = el("span", "badge", count > 9 ? "9+" : String(count));
    btn.appendChild(badge);
  }
}

export function invalidateCards(names) {
  // Un dépôt ou une clôture en langage naturel (refresh_cards:["foryou"])
  // rend la restitution obsolète : on remet l'état en cache à null pour
  // forcer un nouveau fetch au prochain tap (canal pull, jamais poussé).
  if (names && names.includes("foryou")) {
    foryouState.items = null;
    foryouState.fetchedAt = null;
  }
}

export function flashCards(names) {
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
