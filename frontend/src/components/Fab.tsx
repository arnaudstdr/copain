// ── Bouton flottant « + » global (ouvre la feuille d'actions du FAB) ──────────
// Présent sur les 4 onglets (rendu dans App, hors des écrans → un seul
// exemplaire). Sa position basse est ajustée par `#app[data-tab]` quand une
// barre de saisie occupe déjà le bas (Accueil, Chat), cf. index.css.

import { Plus } from "lucide-react";

export function Fab({ onClick }: { onClick: () => void }) {
  return (
    <button className="fab" type="button" aria-label="Ajouter" onClick={onClick}>
      <Plus size={26} strokeWidth={2.4} />
    </button>
  );
}
