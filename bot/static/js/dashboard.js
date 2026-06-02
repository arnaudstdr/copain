// ── Dashboard : chargement, renderers de cards, budget, actu ──────────────
import {
  API_KEY, API_BASE,
  dashboardData, setDashboardData,
  newsState,
} from "./state.js";
import {
  el, makeHead,
  sameDay, formatHM, formatRelativeDay, formatRelativeAge, isAllDayEvent,
  showToast,
} from "./ui.js";
import { openMarkdownView } from "./markdown.js";
// Cycle d'import dashboard ↔ overlays accepté (cf. PROGRESS.md) : les cards
// tappables ouvrent les overlays, et les overlays rafraîchissent le
// dashboard à la fermeture. Bénin : fonctions hoistées, appelées au runtime.
import { openWeather, openEvents, openTasks } from "./overlays.js";

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

export function renderBellBadge(count) {
  const btn = document.getElementById("bell-btn");
  btn.querySelector(".badge")?.remove();
  if (count > 0) {
    const badge = el("span", "badge", count > 9 ? "9+" : String(count));
    btn.appendChild(badge);
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
