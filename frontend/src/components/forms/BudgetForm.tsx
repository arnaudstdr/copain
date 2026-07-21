// ── Formulaire de saisie budgétaire directe (SANS LLM) ───────────────────────
// Extrait de l'ex-BudgetOverlay (step 03) pour être réutilisé à la fois par
// l'écran Budget (onglet) et la feuille du FAB (step 05). POST /expenses
// (spend/income/tick_recurring) — canal parallèle à l'intent=expense du bot,
// réutilisant les mêmes méthodes ExpenseManager côté backend (zéro divergence).
// Le front ne calcule JAMAIS le budget : il affiche ce que renvoie GET /budget.

import { useState } from "react";
import { ApiError, apiPost } from "../../api/client";
import type {
  BudgetMonthDetail,
  ExpenseAction,
  ExpenseCreate,
  ExpenseCreateResponse,
  ExpenseDraft,
} from "../../api/types";
import { formatEur } from "../../lib/format";
import { useToast } from "../Toast";

function todayIso(): string {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

// draft.action vient du bloc <meta> du LLM (typé string côté API, aucune
// contrainte Literal Pydantic) : on valide à l'exécution avant de l'injecter
// dans l'état, faute de quoi le <select> n'aurait aucune option sélectionnée.
function isExpenseAction(value: string | undefined): value is ExpenseAction {
  return value === "spend" || value === "income" || value === "tick_recurring";
}

export function BudgetForm({
  data,
  draft,
  onSubmitted,
}: {
  data: BudgetMonthDetail;
  // Brouillon lu d'une capture (Revolut) via vision → pré-remplissage du
  // formulaire (aucune écriture serveur avant confirmation). Absent lors d'une
  // ouverture normale.
  draft?: ExpenseDraft | null;
  onSubmitted: () => void;
}) {
  const toast = useToast();
  const pending = data.pending;
  const [action, setAction] = useState<ExpenseAction>(
    isExpenseAction(draft?.action) ? draft?.action : "spend",
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
    } catch (err) {
      // 4xx = refus explicite du backend (montant invalide, récurrente inconnue,
      // date invalide…) : on remonte son message pour que l'utilisateur sache
      // quoi corriger. Réseau/timeout (status 0) ou 5xx → message générique.
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        toast(err.message || "Saisie refusée");
      } else {
        toast("Enregistrement impossible");
      }
    } finally {
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
        {action === "tick_recurring" && (
          <span className="budget-label">Laisser vide = montant par défaut de la récurrente</span>
        )}
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
