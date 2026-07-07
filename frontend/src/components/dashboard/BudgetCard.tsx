// Card Budget (pleine largeur). Restant prévisionnel + enveloppes en barres.
// Iso-visuel de budgetCard() + envelopeRow() de dashboard.js (styles inline
// repris à l'identique, les classes .envelope-* viennent d'index.css).
import { Wallet } from "lucide-react";
import type { BudgetCard as BudgetData, BudgetEnvelopeCard } from "../../api/types";
import { formatEur } from "../../lib/format";
import { Card, CardHead } from "./Card";

interface Props {
  budget: BudgetData | null;
  onOpen: () => void;
}

function EnvelopeRow({ env }: { env: BudgetEnvelopeCard }) {
  const ratio = env.allocated_eur > 0 ? Math.min(1, env.spent_eur / env.allocated_eur) : 0;
  const fillClass = env.is_overrun
    ? "envelope-fill overrun"
    : env.shared
      ? "envelope-fill shared"
      : "envelope-fill";
  return (
    <div className={`envelope-row${env.shared ? " shared" : ""}`} style={{ fontSize: "0.85em", marginTop: "4px" }}>
      <div className="envelope-line" style={{ display: "flex", justifyContent: "space-between" }}>
        <span className="envelope-label-wrap">
          <span className="envelope-label">{env.label}</span>
          {env.shared && <span className="envelope-shared-badge">compte joint</span>}
        </span>
        <span className={`envelope-amounts${env.is_overrun ? " envelope-amount-overrun" : ""}`}>
          {`${formatEur(env.remaining_eur)} / ${formatEur(env.allocated_eur)}`}
        </span>
      </div>
      <div className="envelope-track">
        <div className={fillClass} style={{ width: `${ratio * 100}%` }} />
      </div>
    </div>
  );
}

export function BudgetCard({ budget, onOpen }: Props) {
  if (!budget) {
    return (
      <Card empty>
        <CardHead icon={Wallet} label="Budget" />
        <div className="card-primary">Non configuré</div>
        <div className="card-secondary">Ajoute la section `finances` dans data/profile.yaml</div>
      </Card>
    );
  }

  const pendingLabel =
    budget.pending_recurring_count === 1
      ? "1 récurrente à pointer"
      : `${budget.pending_recurring_count} récurrentes à pointer`;

  return (
    <Card tappable onClick={onOpen}>
      <CardHead icon={Wallet} label="Budget" />
      <div className="card-primary" style={budget.remaining_eur < 0 ? { color: "rgb(var(--red))" } : undefined}>
        {`Restant : ${formatEur(budget.remaining_eur)}`}
      </div>
      <div className="card-secondary">{`Épargné cette année : ${formatEur(budget.saved_this_year_eur)}`}</div>
      {budget.pending_recurring_count > 0 && (
        <div className="card-meta" style={budget.has_overdue ? { color: "rgb(var(--red))" } : undefined}>
          {pendingLabel}
        </div>
      )}
      {budget.envelopes.length > 0 && (
        <div className="budget-envelopes" style={{ marginTop: "8px" }}>
          {budget.envelopes.map((env) => (
            <EnvelopeRow key={env.category} env={env} />
          ))}
        </div>
      )}
    </Card>
  );
}
