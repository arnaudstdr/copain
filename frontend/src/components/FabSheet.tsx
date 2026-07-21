// ── Feuille d'actions du FAB (dépôt / dépense) ───────────────────────────────
// Ouverte par le FAB global. Deux entrées → les formulaires extraits au step 03
// (components/forms/), montés dans le shell Overlay (backdrop + entête + tap-fond,
// pas de nouvelle modale). Aucun LLM : POST /thoughts (dépôt) et POST /expenses
// (dépense), les deux canaux directs existants — zéro logique dupliquée. La vue
// « dépense » charge d'abord GET /budget (BudgetForm a besoin du détail du cycle)
// avec le même fail-soft que l'écran Budget.

import { useCallback, useEffect, useState } from "react";
import { Brain, ChevronLeft, PlusCircle, Wallet } from "lucide-react";
import { apiGet } from "../api/client";
import type { BudgetMonthDetail } from "../api/types";
import { Overlay } from "./Overlay";
import { BudgetForm } from "./forms/BudgetForm";
import { DepotForm } from "./forms/DepotForm";

type View = "menu" | "depot" | "expense";

interface Props {
  onClose: () => void;
  // Appelé après un dépôt/dépense réussi (fermeture + resync côté App). Le toast
  // est déjà émis par le formulaire lui-même.
  onSubmitted: () => void;
}

export function FabSheet({ onClose, onSubmitted }: Props) {
  const [view, setView] = useState<View>("menu");

  if (view === "menu") {
    return (
      <Overlay icon={PlusCircle} title="Ajouter" onClose={onClose}>
        <div className="action-list">
          <button className="action-row" type="button" onClick={() => setView("depot")}>
            <span className="action-icon depot">
              <Brain size={18} />
            </span>
            <span className="action-body">
              <span className="action-title">Déposer une pensée</span>
              <span className="action-sub">Vide-toi la tête, je garde.</span>
            </span>
          </button>
          <button className="action-row" type="button" onClick={() => setView("expense")}>
            <span className="action-icon expense">
              <Wallet size={18} />
            </span>
            <span className="action-body">
              <span className="action-title">Ajouter une dépense</span>
              <span className="action-sub">Dépense, revenu ou récurrente.</span>
            </span>
          </button>
        </div>
      </Overlay>
    );
  }

  if (view === "depot") {
    return (
      <Overlay icon={Brain} title="Déposer une pensée" onClose={onClose}>
        <div className="sheet-body">
          <BackRow onBack={() => setView("menu")} />
          <DepotForm onSubmitted={onSubmitted} />
        </div>
      </Overlay>
    );
  }

  return (
    <Overlay icon={Wallet} title="Ajouter une dépense" onClose={onClose}>
      <ExpenseSheet onBack={() => setView("menu")} onSubmitted={onSubmitted} />
    </Overlay>
  );
}

// Retour au menu de la feuille sans la fermer (onClose ferme tout). Réutilise le
// style de bouton subtil existant (même chevron que l'en-tête de MarkdownView).
function BackRow({ onBack }: { onBack: () => void }) {
  return (
    <button type="button" className="action-btn action-btn--subtle" onClick={onBack}>
      <ChevronLeft size={16} />
      Retour
    </button>
  );
}

// Charge le détail du cycle avant de monter BudgetForm (même fail-soft que
// BudgetScreen : le front ne calcule jamais le budget).
function ExpenseSheet({ onBack, onSubmitted }: { onBack: () => void; onSubmitted: () => void }) {
  const [data, setData] = useState<BudgetMonthDetail | null>(null);
  const [status, setStatus] = useState<"loading" | "error" | "ready">("loading");

  // `isActive` laisse le chargement initial (effet) court-circuiter ses setState
  // après démontage ; les rappels manuels (« Réessayer ») passent le défaut.
  const load = useCallback(async (isActive: () => boolean = () => true) => {
    setStatus("loading");
    try {
      const detail = await apiGet<BudgetMonthDetail>("/budget");
      if (isActive()) {
        setData(detail);
        setStatus("ready");
      }
    } catch {
      if (isActive()) setStatus("error");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void load(() => !cancelled);
    return () => {
      cancelled = true;
    };
  }, [load]);

  if (status === "error") {
    return (
      <div className="sheet-body">
        <BackRow onBack={onBack} />
        <p className="placeholder-text">Impossible de charger</p>
        <button type="button" className="action-btn action-btn--subtle" onClick={() => void load()}>
          Réessayer
        </button>
      </div>
    );
  }

  if (status !== "ready" || !data) {
    return (
      <div className="sheet-body">
        <BackRow onBack={onBack} />
        <p className="placeholder-text">Chargement…</p>
      </div>
    );
  }

  return (
    <div className="sheet-body">
      <BackRow onBack={onBack} />
      <BudgetForm data={data} onSubmitted={onSubmitted} />
    </div>
  );
}
