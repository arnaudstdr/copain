// ── Overlay Budget (panneau interactif : saisie directe + récap) ─────────────
// GET /budget (récap détaillé) + formulaire POST /expenses (spend/income/
// tick_recurring, SANS LLM — canal parallèle à l'intent=expense du bot,
// réutilisant les mêmes méthodes ExpenseManager côté backend, donc zéro
// divergence de calcul). Le front ne calcule JAMAIS le budget : il affiche ce
// que renvoie GET /budget. Iso-fonctionnel de openBudget()/renderBudgetForm()/
// submitExpense()/renderBudgetDetail() de bot/static/js/dashboard.js.

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Wallet } from "lucide-react";
import { apiGetBlob, apiGet, apiPost } from "../../api/client";
import type {
  BudgetMonthDetail,
  ExpenseAction,
  ExpenseCreate,
  ExpenseCreateResponse,
  ExpenseDraft,
} from "../../api/types";
import { formatEur } from "../../lib/format";
import { Overlay, PanelEmpty } from "../Overlay";
import { useToast } from "../Toast";

interface Props {
  onClose: () => void;
  // Brouillon lu d'une capture (Revolut) via vision → pré-remplissage du
  // formulaire (aucune écriture serveur avant confirmation). Câblé au composer
  // au step 08 ; absent lors d'une ouverture normale par tap.
  draft?: ExpenseDraft | null;
}

type Status = "loading" | "error" | "ready";

function todayIso(): string {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

export function BudgetOverlay({ onClose, draft }: Props) {
  const [data, setData] = useState<BudgetMonthDetail | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  // Remonte à chaque saisie enregistrée → remonte le formulaire à ses défauts
  // (comme le re-openBudget() du vanilla). Le draft n'agit qu'au 1er montage.
  const [formKey, setFormKey] = useState(0);

  const load = useCallback(async () => {
    try {
      const detail = await apiGet<BudgetMonthDetail>("/budget");
      setData(detail);
      setStatus("ready");
    } catch {
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onSubmitted = () => {
    setFormKey((k) => k + 1);
    void load();
  };

  return (
    <Overlay icon={Wallet} title="Budget" onClose={onClose}>
      <div className="panel-list" id="budget-list">
        {status === "loading" ? (
          <PanelEmpty>Chargement…</PanelEmpty>
        ) : status === "error" || !data ? (
          <PanelEmpty>Impossible de charger</PanelEmpty>
        ) : (
          <>
            <BudgetForm
              key={formKey}
              data={data}
              draft={formKey === 0 ? draft : undefined}
              onSubmitted={onSubmitted}
            />
            <BudgetDetail data={data} />
          </>
        )}
      </div>
    </Overlay>
  );
}

// ── Formulaire de saisie directe ──────────────────────────────────────────────

function BudgetForm({
  data,
  draft,
  onSubmitted,
}: {
  data: BudgetMonthDetail;
  draft?: ExpenseDraft | null;
  onSubmitted: () => void;
}) {
  const toast = useToast();
  const pending = data.pending;
  const [action, setAction] = useState<ExpenseAction>(
    (draft?.action as ExpenseAction | undefined) ?? "spend",
  );
  const [recurringKey, setRecurringKey] = useState<string>(
    draft?.recurring_key ?? pending[0]?.key ?? "",
  );
  const [amount, setAmount] = useState<string>(
    draft?.amount_eur != null ? String(draft.amount_eur) : "",
  );
  const [label, setLabel] = useState<string>(draft?.label ?? "");
  const [category, setCategory] = useState<string>(draft?.category ?? "");
  const [date, setDate] = useState<string>(draft?.occurred_on ?? todayIso());
  const [shared, setShared] = useState<boolean>(draft?.shared ?? false);
  const [startsCycle, setStartsCycle] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    const rawAmount = amount.trim().replace(",", ".");
    const amountNum = rawAmount === "" ? null : Number(rawAmount);
    const amountEur = amountNum !== null && !Number.isNaN(amountNum) ? amountNum : null;
    const payload: ExpenseCreate = {
      action,
      amount_eur: amountEur,
      label: label.trim() || null,
      category: category.trim() || null,
      occurred_on: date || null,
      shared,
      recurring_key: action === "tick_recurring" ? recurringKey || null : null,
      starts_cycle: action === "income" ? startsCycle : false,
    };

    // Garde-fous côté client (le backend reste l'autorité de validation).
    if ((action === "spend" || action === "income") && (amountEur === null || amountEur <= 0)) {
      toast("Montant requis");
      return;
    }
    if (action === "tick_recurring" && !payload.recurring_key) {
      toast("Aucune récurrente à pointer");
      return;
    }

    setSubmitting(true);
    try {
      const result = await apiPost<ExpenseCreateResponse>("/expenses", payload);
      toast(result.recorded === false ? "Déjà pointé ce cycle" : "Saisie enregistrée");
      onSubmitted();
    } catch {
      toast("Enregistrement impossible");
      setSubmitting(false);
    }
  };

  const amountLabel = action === "tick_recurring" ? "Montant (€) — optionnel" : "Montant (€)";

  return (
    <form
      className="budget-form"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="budget-field">
        <label className="budget-label">Type</label>
        <select
          className="budget-input"
          value={action}
          onChange={(e) => setAction(e.target.value as ExpenseAction)}
        >
          <option value="spend">Dépense</option>
          <option value="income">Revenu</option>
          <option value="tick_recurring">Pointer une récurrente</option>
        </select>
      </div>

      {action === "tick_recurring" && (
        <div className="budget-field">
          <label className="budget-label">Récurrente</label>
          <select
            className="budget-input"
            value={recurringKey}
            onChange={(e) => setRecurringKey(e.target.value)}
          >
            {pending.length === 0 ? (
              <option value="">Aucune récurrente à pointer</option>
            ) : (
              pending.map((p) => (
                <option key={p.key} value={p.key}>
                  {`${p.label} — ${formatEur(p.amount_eur)} (le ${p.day})`}
                </option>
              ))
            )}
          </select>
        </div>
      )}

      <div className="budget-field">
        <label className="budget-label">{amountLabel}</label>
        <input
          className="budget-input"
          type="number"
          step="0.01"
          min="0"
          inputMode="decimal"
          placeholder="0,00"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
      </div>

      {action !== "tick_recurring" && (
        <div className="budget-field">
          <label className="budget-label">Libellé</label>
          <input
            className="budget-input"
            type="text"
            placeholder="ex. courses"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </div>
      )}

      {action === "spend" && (
        <div className="budget-field">
          <label className="budget-label">Catégorie</label>
          <input
            className="budget-input"
            type="text"
            placeholder="ex. alimentation"
            list="bf-category-list"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          />
          <datalist id="bf-category-list">
            {data.envelopes.map((env) => (
              <option key={env.category} value={env.category} />
            ))}
          </datalist>
        </div>
      )}

      <div className="budget-field">
        <label className="budget-label">Date</label>
        <input
          className="budget-input"
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
      </div>

      {action === "spend" && (
        <label className="budget-check">
          <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
          <span>Compte joint</span>
        </label>
      )}

      {action === "income" && (
        <label className="budget-check">
          <input
            type="checkbox"
            checked={startsCycle}
            onChange={(e) => setStartsCycle(e.target.checked)}
          />
          <span>C'est mon salaire (démarre un cycle)</span>
        </label>
      )}

      <button className="budget-submit" type="submit" disabled={submitting}>
        Enregistrer
      </button>
    </form>
  );
}

// ── Récap (markdown-body iso-visuel) + export CSV ────────────────────────────

function BudgetDetail({ data }: { data: BudgetMonthDetail }) {
  const toast = useToast();

  const exportCsv = async () => {
    // Bornes du cycle budgétaire courant. Fallback (réponse sans cycle_end) :
    // fin du mois civil du début — repris verbatim du vanilla.
    const start = data.cycle_start || data.month;
    let end = data.cycle_end;
    if (!end) {
      const startDate = new Date(`${start}T00:00:00`);
      const lastDay = new Date(startDate.getFullYear(), startDate.getMonth() + 1, 0);
      const mm = String(lastDay.getMonth() + 1).padStart(2, "0");
      const dd = String(lastDay.getDate()).padStart(2, "0");
      end = `${lastDay.getFullYear()}-${mm}-${dd}`;
    }
    try {
      const blob = await apiGetBlob(
        `/expenses/export.csv?from=${start}&to=${end}`,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `copain-depenses-${start}_${end}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast("Export CSV téléchargé");
    } catch {
      toast("Export CSV impossible");
    }
  };

  return (
    <div className="budget-detail">
      <BudgetRecap data={data} />
      <button className="budget-export" type="button" onClick={() => void exportCsv()}>
        Exporter CSV
      </button>
    </div>
  );
}

const WARN = <AlertTriangle size={14} className="lucide-warn" />;

function BudgetRecap({ data }: { data: BudgetMonthDetail }) {
  const startIso = data.cycle_start || data.month;
  const fmtShort = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString("fr-FR", { day: "numeric", month: "long" });

  let header: string;
  if (data.cycle_end) {
    header = `Cycle du ${fmtShort(startIso)} au ${fmtShort(data.cycle_end)}`;
  } else {
    const monthLabel = new Date(`${startIso}T00:00:00`).toLocaleDateString("fr-FR", {
      month: "long",
      year: "numeric",
    });
    header = monthLabel.charAt(0).toUpperCase() + monthLabel.slice(1);
  }

  return (
    <div className="markdown-body budget-recap">
      <h2>{header}</h2>
      <p>
        <strong>{`Restant prévisionnel : ${formatEur(data.remaining_eur)}`}</strong>
      </p>
      <ul>
        <li>{`Revenu : ${formatEur(data.income_eur)}`}</li>
        <li>{`Récurrentes pointées : ${formatEur(data.spent_recurring_eur)}`}</li>
        <li>{`Ponctuelles : ${formatEur(data.spent_punctual_eur)}`}</li>
        <li>{`Épargne ce cycle : ${formatEur(data.saved_this_month_eur)}`}</li>
        <li>{`Épargné cette année : ${formatEur(data.saved_this_year_eur)}`}</li>
      </ul>

      {data.envelopes.length > 0 && (
        <>
          <h3>Enveloppes</h3>
          <ul>
            {data.envelopes.map((env) => (
              <li key={env.category}>
                <strong>{env.label}</strong>
                {env.shared && (
                  <>
                    {" "}
                    <em>(compte joint)</em>
                  </>
                )}
                {` : ${formatEur(env.spent_eur)} / ${formatEur(env.allocated_eur)}`}
                {env.is_overrun && (
                  <>
                    {" "}
                    {WARN} {`dépassement de ${formatEur(env.overrun_eur)}`}
                  </>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      {data.pending.length > 0 && (
        <>
          <h3>{`À pointer (${data.pending.length})`}</h3>
          <ul>
            {data.pending.map((p) => (
              <li key={p.key}>
                <strong>{p.label}</strong>
                {` ${formatEur(p.amount_eur)}, prévu le ${p.day}`}
                {p.kind === "saving" && " (épargne)"}
                {p.is_overdue && (
                  <>
                    {" "}
                    {WARN} en retard
                  </>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      {data.transactions.length > 0 ? (
        <>
          <h3>Transactions du mois</h3>
          <ul>
            {data.transactions.map((t) => {
              const day = new Date(`${t.occurred_on}T00:00:00`).toLocaleDateString("fr-FR", {
                day: "numeric",
                month: "short",
              });
              const sign = t.kind === "income" ? "+" : "−";
              return (
                <li key={t.id}>
                  {`${day} — ${t.label}`}
                  {t.category && ` (${t.category})`}
                  {t.shared && (
                    <>
                      {" "}
                      <em>(compte joint)</em>
                    </>
                  )}
                  {` : ${sign}${formatEur(t.amount_eur)}`}
                </li>
              );
            })}
          </ul>
        </>
      ) : (
        <p>
          <em>Aucune transaction enregistrée ce mois.</em>
        </p>
      )}
    </div>
  );
}
