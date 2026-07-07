// ── Bulle éphémère (réponse /ask côté dashboard) ────────────────────────────
// Portage de showEphemeral() de bot/static/js/ui.js : la réponse texte d'un
// envoi /ask depuis le dashboard s'affiche en bulle éphémère (NON persistée,
// contrairement au chat), auto-effacée après 8 s ou au tap. Réponse du bot =
// markdown ; erreur = pictogramme + message FR (pré-wrap conservé).

import { useEffect, useRef } from "react";
import { AlertTriangle } from "lucide-react";
import { Markdown } from "./Markdown";
import { ActionButtons } from "./ActionButtons";
import type { Action } from "../api/types";

export interface EphemeralData {
  text: string;
  isError: boolean;
  actions?: Action[];
}

export function Ephemeral({ data, onHide }: { data: EphemeralData; onHide: () => void }) {
  // onHide lu via ref : le timer ne dépend que du contenu, donc il n'est PAS
  // réarmé par un re-render du parent (ex. refresh dashboard 120 s).
  const onHideRef = useRef(onHide);
  onHideRef.current = onHide;

  // Auto-masquage 8 s, réarmé uniquement à chaque nouveau contenu. Quand la
  // réponse porte une action tappable, on NE l'arme PAS : la bulle (et ses
  // boutons) reste jusqu'à ce que l'utilisateur tape une action ou la ferme.
  const hasActions = (data.actions?.length ?? 0) > 0;
  useEffect(() => {
    if (hasActions) return;
    const id = setTimeout(() => onHideRef.current(), 8000);
    return () => clearTimeout(id);
  }, [data, hasActions]);

  return (
    <div
      id="ephemeral"
      onClick={onHide}
      style={{ borderColor: data.isError ? "rgb(var(--red))" : "rgb(var(--border2))" }}
    >
      {data.isError ? (
        <span>
          <AlertTriangle size={16} className="lucide-warn" /> {data.text}
        </span>
      ) : (
        <>
          <Markdown className="chat-md">{data.text}</Markdown>
          <ActionButtons actions={data.actions} />
        </>
      )}
    </div>
  );
}
