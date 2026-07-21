// ── Sheet d'édition/suppression d'une écriture budgétaire (SANS LLM) ─────────
// Ouverte au tap sur une transaction de l'onglet Budget. Réutilise le shell
// Overlay (comme FabSheet) + les primitives de formulaire de BudgetForm
// (.budget-field/.budget-input). PATCH /expenses/{id} (édition partielle) et
// DELETE /expenses/{id} (suppression, confirmation sobre en deux temps). Le
// front ne calcule JAMAIS le budget : après succès on délègue le refetch au
// parent (`onMutated`) avant de fermer.
//
// `kind`/`recurring_key` ne sont pas éditables (SPEC/step 03) : affichés en
// lecture seule pour le contexte, jamais envoyés. `category`/`shared` ne sont
// exposés que pour une ponctuelle (comme la saisie d'une dépense).

import { useState } from "react";
import { Wallet } from "lucide-react";
import { ApiError, apiDelete, apiPatch } from "../api/client";
import type { BudgetEnvelopeDetail, BudgetTransaction, ExpenseUpdate } from "../api/types";
import { formatEur } from "../lib/format";
import { Overlay } from "./Overlay";
import { useToast } from "./Toast";

const KIND_LABELS: Record<string, string> = {
  punctual: "Dépense ponctuelle",
  recurring_tick: "Récurrente pointée",
  saving_tick: "Épargne pointée",
  income: "Revenu",
};

interface Props {
  transaction: BudgetTransaction;
  // Enveloppes du cycle → autocomplétion de la catégorie (comme BudgetForm).
  envelopes: BudgetEnvelopeDetail[];
  onClose: () => void;
  // Refetch + resync dashboard, résolus par le parent AVANT la fermeture.
  onMutated: () => Promise<void>;
}

export function ExpenseEditSheet({ transaction: t, envelopes, onClose, onMutated }: Props) {
  const toast = useToast();
  // Seule une ponctuelle porte catégorie + compte joint (cf. saisie d'une dépense).
  const isPunctual = t.kind === "punctual";

  const [amount, setAmount] = useState<string>(String(t.amount_eur));
  const [label, setLabel] = useState<string>(t.label);
  const [category, setCategory] = useState<string>(t.category ?? "");
  const [date, setDate] = useState<string>(t.occurred_on);
  const [shared, setShared] = useState<boolean>(t.shared);
  // Un seul verrou pour PATCH et DELETE : désactive tous les boutons en vol.
  const [busy, setBusy] = useState(false);
  // Suppression en deux temps (pas de modale dramatique) : 1er tap arme, 2e confirme.
  const [confirmDelete, setConfirmDelete] = useState(false);

  const save = async () => {
    const rawAmount = amount.trim().replace(",", ".");
    const amountNum = rawAmount === "" ? null : Number(rawAmount);
    const amountEur = amountNum !== null && !Number.isNaN(amountNum) ? amountNum : null;
    // Garde-fou client (le backend reste l'autorité : 400 si montant ≤ 0).
    if (amountEur === null || amountEur <= 0) {
      toast("Montant requis");
      return;
    }

    const payload: ExpenseUpdate = {
      amount_eur: amountEur,
      label: label.trim() || null,
      occurred_on: date || null,
    };
    // `category`/`shared` uniquement pour une ponctuelle (pas envoyés sinon).
    if (isPunctual) {
      // Chaîne vide (et non `null`) quand la catégorie est effacée : côté
      // backend `null` = « champ non fourni » (skip), alors qu'une chaîne vide
      // efface réellement la catégorie. Sans ça, vider la catégorie serait un
      // no-op silencieux malgré le toast de succès.
      payload.category = category.trim();
      payload.shared = shared;
    }

    setBusy(true);
    try {
      await apiPatch<BudgetTransaction>(`/expenses/${t.id}`, payload);
      await onMutated();
      toast("Modification enregistrée");
      onClose();
    } catch (err) {
      // 4xx = refus explicite (montant/date invalide, 404) → message backend FR ;
      // réseau/timeout (status 0) ou 5xx → message générique. Écran conservé.
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        toast(err.message || "Modification refusée");
      } else {
        toast("Modification impossible");
      }
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await apiDelete(`/expenses/${t.id}`);
      await onMutated();
      toast("Écriture supprimée");
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        toast(err.message || "Suppression refusée");
      } else {
        toast("Suppression impossible");
      }
      setConfirmDelete(false);
    } finally {
      setBusy(false);
    }
  };

  const kindLabel = KIND_LABELS[t.kind] ?? t.kind;

  return (
    <Overlay icon={Wallet} title="Modifier l'écriture" onClose={onClose}>
      <div className="sheet-body">
        <form
          className="budget-form"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          {/* Contexte non éditable : type d'écriture (+ récurrente si pointage). */}
          <div className="budget-context">
            {kindLabel}
            {t.recurring_key && ` · ${t.recurring_key}`}
          </div>

          <div className="budget-field">
            <label className="budget-label">Montant (€)</label>
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

          {isPunctual && (
            <div className="budget-field">
              <label className="budget-label">Catégorie</label>
              <input
                className="budget-input"
                type="text"
                placeholder="ex. alimentation"
                list="ees-category-list"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              />
              <datalist id="ees-category-list">
                {envelopes.map((env) => (
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

          {isPunctual && (
            <label className="budget-check">
              <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
              <span>Compte joint</span>
            </label>
          )}

          <button className="budget-submit" type="submit" disabled={busy}>
            Enregistrer
          </button>
        </form>

        <div className="budget-danger">
          {confirmDelete ? (
            <>
              <span className="budget-danger-ask">{`Supprimer « ${t.label} » (${formatEur(t.amount_eur)}) ?`}</span>
              <div className="budget-danger-actions">
                <button
                  type="button"
                  className="budget-delete"
                  disabled={busy}
                  onClick={() => void remove()}
                >
                  Confirmer
                </button>
                <button
                  type="button"
                  className="action-btn action-btn--subtle"
                  disabled={busy}
                  onClick={() => setConfirmDelete(false)}
                >
                  Annuler
                </button>
              </div>
            </>
          ) : (
            <button
              type="button"
              className="budget-delete"
              disabled={busy}
              onClick={() => setConfirmDelete(true)}
            >
              Supprimer
            </button>
          )}
        </div>
      </div>
    </Overlay>
  );
}
