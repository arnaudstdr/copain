// ── Boutons d'action proposés par copain ────────────────────────────────────
// copain PROPOSE, l'utilisateur TAPE : chaque action porte un deep-link construit
// et validé côté serveur (`open`), ouvert par un simple <a href> — l'OS gère le
// schéma. Rien ne s'exécute sans un tap. Pas de target="_blank" (un schéma custom
// y ouvrirait un onglet vide). Rendu sous une réponse de copain (bulle du fil,
// bulle éphémère du dashboard).

import { Navigation } from "lucide-react";
import type { Action } from "../api/types";

// type d'action → pictogramme (catalogue fermé, aligné sur le modèle Pydantic).
const ICON: Record<string, typeof Navigation> = { navigate: Navigation };

export function ActionButtons({ actions }: { actions?: Action[] }) {
  if (!actions || actions.length === 0) return null;
  return (
    <div className="msg-actions">
      {actions.map((action, i) => {
        const Icon = ICON[action.type];
        return (
          <a key={i} className="action-btn" href={action.open} rel="noopener noreferrer">
            {Icon && <Icon size={14} />}
            {action.label}
          </a>
        );
      })}
    </div>
  );
}
