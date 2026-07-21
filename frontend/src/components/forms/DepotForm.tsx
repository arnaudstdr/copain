// ── Formulaire de dépôt express (décharge cognitive directe, SANS LLM) ───────
// Extrait de l'ex-DepotExpressOverlay (step 03) pour être monté dans la feuille
// du FAB (step 05). POST /thoughts (content + kind optionnel) → accusé sobre.
// Le chip de type ne sert plus qu'à TAGUER le prochain dépôt (le listage des
// dépôts existants vit désormais dans l'onglet Pensées, panneau « Mes dépôts »).
// Un dépôt invalide le cache « Pour toi » (restitution obsolète).

import { useState } from "react";
import { apiPost } from "../../api/client";
import type { ThoughtCreateResponse, ThoughtKind } from "../../api/types";
import { invalidateForYou } from "../../lib/foryouCache";
import { useToast } from "../Toast";

const KINDS: { kind: ThoughtKind; label: string }[] = [
  { kind: "worry", label: "Souci" },
  { kind: "idea", label: "Idée" },
  { kind: "note", label: "Note" },
];

interface Props {
  // Appelé après un dépôt réussi (fermer la feuille, rafraîchir le dashboard).
  onSubmitted?: () => void;
}

export function DepotForm({ onSubmitted }: Props) {
  const toast = useToast();
  const [content, setContent] = useState("");
  const [selectedKind, setSelectedKind] = useState<ThoughtKind | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const toggleChip = (kind: ThoughtKind) =>
    setSelectedKind((prev) => (prev === kind ? null : kind));

  const submit = async () => {
    const trimmed = content.trim();
    if (!trimmed) {
      toast("Rien à déposer");
      return;
    }
    setSubmitting(true);
    try {
      const data = await apiPost<ThoughtCreateResponse>("/thoughts", {
        content: trimmed,
        kind: selectedKind,
      });
      toast(data.ack || "C'est posé.");
      // Un nouveau dépôt rend la restitution « Pour toi » obsolète (refetch au tap).
      invalidateForYou();
      // Réinitialise le formulaire sans dépendre d'un démontage parent
      // (onSubmitted est optionnel : la feuille peut rester montée).
      setContent("");
      setSelectedKind(null);
      onSubmitted?.();
    } catch {
      toast("Impossible de déposer");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="depot-form">
      <textarea
        className="depot-input"
        rows={4}
        placeholder="Vide-toi la tête… je garde, tu n'as plus à y penser."
        value={content}
        onChange={(e) => setContent(e.target.value)}
        autoFocus
      />
      <div className="depot-chips" role="group" aria-label="Type (optionnel)">
        {KINDS.map(({ kind, label }) => (
          <button
            key={kind}
            type="button"
            className={`depot-chip${selectedKind === kind ? " selected" : ""}`}
            onClick={() => toggleChip(kind)}
          >
            {label}
          </button>
        ))}
      </div>
      <button
        type="button"
        className="depot-submit"
        disabled={submitting}
        onClick={() => void submit()}
      >
        Déposer
      </button>
    </div>
  );
}
