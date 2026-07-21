// ── Onglet Budget (récap cycle + formulaire de saisie directe) ──────────────
// Reprend le contenu de l'ex-BudgetOverlay (step 03) : GET /budget (récap
// détaillé) + BudgetForm (POST /expenses, extrait dans components/forms/). Le
// front ne calcule JAMAIS le budget : il affiche ce que renvoie GET /budget.
// L'ouverture pré-remplie via `draft` (photo de ticket) est conservée ; une
// saisie enregistrée rafraîchit le dashboard (`onChanged`).

import { Fragment, useCallback, useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { apiGetBlob, apiGet } from "../api/client";
import type {
  BudgetMonthDetail,
  BudgetEnvelopeDetail,
  BudgetPendingItem,
  BudgetTransaction,
  ExpenseDraft,
} from "../api/types";
import { formatDaySeparator, formatEur } from "../lib/format";
import { BudgetForm } from "../components/forms/BudgetForm";
import { BudgetSparkline } from "../components/BudgetSparkline";
import { ExpenseEditSheet } from "../components/ExpenseEditSheet";
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
  // Transaction en cours d'édition (tap sur une row) → sheet montée par-dessus.
  const [editing, setEditing] = useState<BudgetTransaction | null>(null);

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

  // Après édition/suppression via la sheet : refetch (le front ne recalcule pas)
  // puis resync du dashboard. Ne remonte PAS le formulaire (la sheet est un
  // canal séparé). Fail-soft comme onSubmitted : sur échec on garde l'affichage.
  const refreshAfterMutation = async () => {
    try {
      const detail = await fetchBudget();
      setData(detail);
      setError(false);
    } catch {
      setError(true);
    }
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
              <BudgetHero data={data} />
              <BudgetSparkline
                curve={data.spend_curve}
                spendableEur={data.spendable_eur}
                cycleStart={data.cycle_start}
                horizon={data.spend_horizon}
              />
              <EnvelopesSection envelopes={data.envelopes} />
              <PendingSection pending={data.pending} />
              <BudgetForm
                key={formKey}
                data={data}
                draft={formKey === 0 ? draft : undefined}
                onSubmitted={() => void onSubmitted()}
              />
              <TransactionsSection transactions={data.transactions} onEdit={setEditing} />
              <ExportButton data={data} />
            </>
          )}
        </div>
      </div>
      {editing && data && (
        <ExpenseEditSheet
          transaction={editing}
          envelopes={data.envelopes}
          onClose={() => setEditing(null)}
          onMutated={refreshAfterMutation}
        />
      )}
    </div>
  );
}

// ── Héros : restant prévisionnel + projection fin de cycle ───────────────────

function BudgetHero({ data }: { data: BudgetMonthDetail }) {
  // Ambre (jamais rouge) quand le restant prévisionnel passe négatif.
  const amber = data.remaining_eur < 0;
  // Jour du salaire (jour 0) : aucun jour écoulé → rythme nul et projection ==
  // restant. On masque alors la ligne de projection (rien à extrapoler).
  const dayZero = data.daily_rate_eur === 0 && data.projected_remaining_eur === data.remaining_eur;

  return (
    <section className="budget-hero">
      <div className="budget-hero-label">Restant prévisionnel</div>
      <div className={`budget-hero-amount${amber ? " is-amber" : ""}`}>
        {formatEur(data.remaining_eur)}
      </div>
      {!dayZero && (
        <div className="budget-hero-projection">
          {`À ce rythme : ${formatEur(data.projected_remaining_eur)} en fin de cycle`}
        </div>
      )}
    </section>
  );
}

// ── Section « À pointer » : récurrentes en attente de pointage (.group iOS) ───

function PendingSection({ pending }: { pending: BudgetPendingItem[] }) {
  if (pending.length === 0) return null;
  return (
    <>
      <div className="group-label">{`À pointer (${pending.length})`}</div>
      <div className="group">
        {pending.map((p) => (
          <div className="group-row" key={p.key}>
            <div className="row-body">
              <div className="row-title">{p.label}</div>
              <div className="row-sub">
                {`prévu le ${p.day}`}
                {p.kind === "saving" && " · épargne"}
              </div>
            </div>
            {p.is_overdue && <span className="row-pastille">en retard</span>}
            <span className="row-value">{formatEur(p.amount_eur)}</span>
          </div>
        ))}
      </div>
    </>
  );
}

// ── Section « Enveloppes » : barres de progression (step 05) ─────────────────
// Mapping données → pixels : remplissage = dépensé / alloué borné à 100 %, vert
// si sain, ambre si dépassement (+ mention factuelle, jamais rouge). Une
// enveloppe shared (compte joint) est hors budget perso : teinte neutre + badge,
// aucun jugement de santé (pas de vert/ambre).

function EnvelopesSection({ envelopes }: { envelopes: BudgetEnvelopeDetail[] }) {
  if (envelopes.length === 0) return null;
  return (
    <>
      <div className="group-label">Enveloppes</div>
      <div className="group">
        {envelopes.map((env) => {
          const ratio = env.allocated_eur > 0 ? env.spent_eur / env.allocated_eur : 0;
          const width = `${Math.min(Math.max(ratio, 0), 1) * 100}%`;
          const fillClass = env.shared ? " is-shared" : env.is_overrun ? " is-amber" : "";
          return (
            <div className="env-row" key={env.category}>
              <div className="env-head">
                <span className="env-label">
                  {env.label}
                  {env.shared && <span className="env-tag">compte joint</span>}
                </span>
                <span className="env-amounts">
                  {`${formatEur(env.spent_eur)} / ${formatEur(env.allocated_eur)}`}
                </span>
              </div>
              <div className="env-track">
                <div className={`env-fill${fillClass}`} style={{ width }} />
              </div>
              {env.is_overrun && !env.shared && (
                <div className="env-overrun">{`Dépassement de ${formatEur(env.overrun_eur)}`}</div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

const WARN = <AlertTriangle size={14} className="lucide-warn" />;

// ── Section « Transactions » : écritures du cycle groupées par jour ──────────
// Groupage pur front depuis `transactions[]` (déjà chargé, aucun nouvel appel).
// Jours du plus récent au plus ancien, libellé FR (« Aujourd'hui »/« Hier »/date).
// Chaque row est tappable → sheet d'édition. Badge shared neutre conservé.

function groupByDay(transactions: BudgetTransaction[]): { day: string; items: BudgetTransaction[] }[] {
  const byDay = new Map<string, BudgetTransaction[]>();
  for (const t of transactions) {
    const items = byDay.get(t.occurred_on) ?? [];
    items.push(t);
    byDay.set(t.occurred_on, items);
  }
  // Tri décroissant sur la date ISO (comparaison lexicale suffisante en YYYY-MM-DD).
  return [...byDay.entries()]
    .sort(([a], [b]) => (a < b ? 1 : a > b ? -1 : 0))
    .map(([day, items]) => ({ day, items }));
}

function TransactionsSection({
  transactions,
  onEdit,
}: {
  transactions: BudgetTransaction[];
  onEdit: (t: BudgetTransaction) => void;
}) {
  if (transactions.length === 0) {
    return (
      <>
        <div className="group-label">Transactions</div>
        <p className="placeholder-text">Aucune écriture ce cycle.</p>
      </>
    );
  }

  return (
    <>
      {groupByDay(transactions).map(({ day, items }) => (
        <Fragment key={day}>
          <div className="group-label">{formatDaySeparator(new Date(`${day}T00:00:00`))}</div>
          <div className="group">
            {items.map((t) => {
              // Revenu = crédit (+), tout le reste = débit (−), comme l'ex-récap.
              const sign = t.kind === "income" ? "+" : "−";
              return (
                <button className="group-row" type="button" key={t.id} onClick={() => onEdit(t)}>
                  <div className="row-body">
                    <div className="row-title">
                      {t.label}
                      {t.shared && <span className="env-tag">compte joint</span>}
                    </div>
                    {t.category && <div className="row-sub">{t.category}</div>}
                  </div>
                  <span className="row-value">{`${sign}${formatEur(t.amount_eur)}`}</span>
                </button>
              );
            })}
          </div>
        </Fragment>
      ))}
    </>
  );
}

// ── Export CSV du cycle courant ──────────────────────────────────────────────

function ExportButton({ data }: { data: BudgetMonthDetail }) {
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
      <button className="budget-export" type="button" onClick={() => void exportCsv()}>
        Exporter CSV
      </button>
    </div>
  );
}
