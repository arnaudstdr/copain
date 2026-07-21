// ── Onglet Budget (récap cycle + formulaire de saisie directe) ──────────────
// Reprend le contenu de l'ex-BudgetOverlay (step 03) : GET /budget (récap
// détaillé) + BudgetForm (POST /expenses, extrait dans components/forms/). Le
// front ne calcule JAMAIS le budget : il affiche ce que renvoie GET /budget.
// L'ouverture pré-remplie via `draft` (photo de ticket) est conservée ; une
// saisie enregistrée rafraîchit le dashboard (`onChanged`).

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { apiGetBlob, apiGet } from "../api/client";
import type { BudgetMonthDetail, ExpenseDraft } from "../api/types";
import { formatEur } from "../lib/format";
import { BudgetForm } from "../components/forms/BudgetForm";
import { useToast } from "../components/Toast";

interface Props {
  // Brouillon lu d'une capture (Revolut) → pré-remplissage au 1er montage.
  draft: ExpenseDraft | null;
  // Une saisie enregistrée modifie la card Budget de l'Accueil → resync.
  onChanged: () => void;
  // Compteur bumpé par une saisie via le FAB (hors écran) → recharge le récap.
  reloadKey?: number;
}

export function BudgetScreen({ draft, onChanged, reloadKey = 0 }: Props) {
  const [data, setData] = useState<BudgetMonthDetail | null>(null);
  const [loading, setLoading] = useState(true);
  // Erreur découplée de `data` (comme useDashboard) : un refetch raté ne jette
  // pas le dernier récap valide, il est seulement signalé (bandeau discret).
  const [error, setError] = useState(false);
  // Remonte à chaque saisie enregistrée → réinitialise le formulaire à ses
  // défauts. Le draft n'agit qu'au 1er montage (formKey === 0).
  const [formKey, setFormKey] = useState(0);

  // Fetch pur : renvoie le récap ou lève. Le mapping vers l'état appartient à
  // l'appelant, ce qui laisse l'effet protéger ses setState d'une réponse
  // périmée (garde `cancelled`).
  const fetchBudget = useCallback(() => apiGet<BudgetMonthDetail>("/budget"), []);

  // `reloadKey` refait le fetch après une saisie via le FAB (le formulaire du
  // FAB vit hors de cet écran, `onSubmitted` ne relance pas `load` d'ici).
  // Garde anti-course + anti-démontage : une réponse arrivée après un nouveau
  // bump de `reloadKey` (ou après démontage) ne doit pas écraser l'état frais.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchBudget()
      .then((detail) => {
        if (cancelled) return;
        setData(detail);
        setError(false);
      })
      .catch(() => {
        if (cancelled) return;
        setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [fetchBudget, reloadKey]);

  // On attend la résolution du refetch AVANT de remonter le formulaire : sinon
  // `key={formKey}` le remonterait avec un `data` (donc un `pending`) périmé
  // juste après un tick_recurring. Sur échec, on conserve l'affichage courant.
  const onSubmitted = async () => {
    try {
      const detail = await fetchBudget();
      setData(detail);
      setError(false);
    } catch {
      setError(true);
    }
    setFormKey((k) => k + 1);
    onChanged();
  };

  return (
    <div className="screen">
      <header>
        <div className="greeting">
          <div className="greeting-name">Budget</div>
        </div>
      </header>
      <div className="screen-scroll">
        <div className="screen-body">
          {loading && !data ? (
            <p className="placeholder-text">Chargement…</p>
          ) : !data ? (
            <p className="placeholder-text">Impossible de charger</p>
          ) : (
            <>
              {error && (
                <p className="placeholder-text">
                  {WARN} Récap non actualisé (réseau). Dernier récap connu affiché.
                </p>
              )}
              <BudgetForm
                key={formKey}
                data={data}
                draft={formKey === 0 ? draft : undefined}
                onSubmitted={() => void onSubmitted()}
              />
              <BudgetDetail data={data} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Récap (markdown-body iso-visuel) + export CSV ────────────────────────────

function BudgetDetail({ data }: { data: BudgetMonthDetail }) {
  const toast = useToast();

  const exportCsv = async () => {
    // Bornes du cycle budgétaire courant (toujours renseignées côté backend).
    const start = data.cycle_start;
    const end = data.cycle_end;
    try {
      const blob = await apiGetBlob(`/expenses/export.csv?from=${start}&to=${end}`);
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
  const fmtShort = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString("fr-FR", { day: "numeric", month: "long" });

  // cycle_start / cycle_end sont garantis par le contrat backend (non nullable).
  const header = `Cycle du ${fmtShort(data.cycle_start)} au ${fmtShort(data.cycle_end)}`;

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
