// ── Shell d'overlay générique (bottom-sheet de verre) ───────────────────────
// Factorise le backdrop, l'entête (pastille lucide + titre + croix) et la
// fermeture au tap sur le fond, communs à tous les panneaux (notifs, tâches,
// météo, évents ici ; dépôt/pour toi/budget au step 06). Iso-fonctionnel avec
// le front vanilla : ferme au clic sur le fond (event.target === currentTarget)
// et sur la croix, pas de fermeture au clavier (le vanilla n'en a pas).

import type { MouseEvent, ReactNode } from "react";
import { X } from "lucide-react";
import type { LucideIcon } from "lucide-react";

interface Props {
  icon: LucideIcon;
  title: string;
  onClose: () => void;
  children: ReactNode;
}

export function Overlay({ icon: Icon, title, onClose, children }: Props) {
  const onBackdrop = (e: MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose();
  };

  return (
    <div className="overlay" onClick={onBackdrop}>
      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">
            <Icon className="lucide-text" />
            {title}
          </span>
          <button className="close-btn" aria-label="Fermer" type="button" onClick={onClose}>
            <X size={14} strokeWidth={2.2} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** État vide/chargement/erreur d'un panneau (message centré sobre). */
export function PanelEmpty({ children }: { children: ReactNode }) {
  return <div className="panel-empty">{children}</div>;
}
