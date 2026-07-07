// ── Overlay Dépôt express (décharge cognitive directe, SANS LLM) ─────────────
// POST /thoughts (content + kind optionnel) → accusé sobre. Iso-fonctionnel de
// openDepot()/toggleDepotChip()/loadDepotEntries()/submitDepot() de
// bot/static/js/overlays.js. Le chip de type a un DOUBLE RÔLE : taguer le
// prochain dépôt ET lister les dépôts déjà enregistrés de ce type
// (GET /thoughts?kind=, sans LLM), chacun clôturable (« C'est réglé »).
// Un dépôt ou une clôture invalide le cache « Pour toi » (restitution obsolète).

import { useState } from "react";
import { PenLine } from "lucide-react";
import { apiGet, apiPost } from "../../api/client";
import type {
  ThoughtCreateResponse,
  ThoughtItem,
  ThoughtKind,
  ThoughtsListResponse,
} from "../../api/types";
import { formatRelativeAge } from "../../lib/format";
import { invalidateForYou } from "../../lib/foryouCache";
import { Overlay } from "../Overlay";
import { useToast } from "../Toast";

interface Props {
  onClose: () => void;
  // Rafraîchit le dashboard après une action mid-overlay (clôture d'entrée),
  // comme le loadDashboard() du vanilla. La fermeture rafraîchit aussi via onClose.
  onRefresh: () => void;
}

const KINDS: { kind: ThoughtKind; label: string }[] = [
  { kind: "worry", label: "Souci" },
  { kind: "idea", label: "Idée" },
  { kind: "note", label: "Note" },
];

// Effondrement de sortie repris verbatim du vanilla (opacity/max-height/…→0).
const COLLAPSE_STYLE = {
  transition: "opacity 0.25s, max-height 0.25s, padding 0.25s, margin 0.25s",
  opacity: 0,
  maxHeight: 0,
  paddingTop: 0,
  paddingBottom: 0,
  marginTop: 0,
  marginBottom: 0,
} as const;

type EntriesStatus = "idle" | "loading" | "error" | "ready";

export function DepotExpressOverlay({ onClose, onRefresh }: Props) {
  const toast = useToast();
  const [content, setContent] = useState("");
  const [selectedKind, setSelectedKind] = useState<ThoughtKind | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [entries, setEntries] = useState<ThoughtItem[]>([]);
  const [entriesStatus, setEntriesStatus] = useState<EntriesStatus>("idle");

  // Sélection optionnelle et exclusive, désélectionnable. Sélectionner un type
  // charge la liste des dépôts de ce type ; désélectionner la vide.
  const toggleChip = (kind: ThoughtKind) => {
    if (selectedKind === kind) {
      setSelectedKind(null);
      setEntries([]);
      setEntriesStatus("idle");
      return;
    }
    setSelectedKind(kind);
    setEntriesStatus("loading");
    apiGet<ThoughtsListResponse>(`/thoughts?kind=${encodeURIComponent(kind)}`)
      .then((data) => {
        // Course : l'utilisateur a pu re-taper un autre chip entre-temps.
        setEntries(data.thoughts ?? []);
        setEntriesStatus("ready");
      })
      .catch(() => setEntriesStatus("error"));
  };

  const removeEntry = (id: number) => setEntries((prev) => prev.filter((e) => e.id !== id));

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
      onClose(); // ferme + rafraîchit le dashboard (closeAndRefresh côté App)
    } catch {
      setSubmitting(false);
      toast("Impossible de déposer");
    }
  };

  return (
    <Overlay icon={PenLine} title="Dépôt express" onClose={onClose}>
      <div className="panel-list">
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
          <div className="depot-entries">
            {entriesStatus === "loading" && (
              <div className="depot-entries-empty">Chargement…</div>
            )}
            {entriesStatus === "error" && (
              <div className="depot-entries-empty">Impossible de charger les dépôts.</div>
            )}
            {entriesStatus === "ready" && entries.length === 0 && (
              <div className="depot-entries-empty">Rien de ce type pour l'instant.</div>
            )}
            {entriesStatus === "ready" &&
              entries.map((entry) => (
                <DepotEntry
                  key={entry.id}
                  entry={entry}
                  onClosed={() => {
                    invalidateForYou();
                    onRefresh();
                    removeEntry(entry.id);
                  }}
                  onError={() => toast("Impossible de clôturer")}
                />
              ))}
          </div>
        </div>
      </div>
    </Overlay>
  );
}

function DepotEntry({
  entry,
  onClosed,
  onError,
}: {
  entry: ThoughtItem;
  onClosed: () => void;
  onError: () => void;
}) {
  const [closing, setClosing] = useState(false);
  const [collapsing, setCollapsing] = useState(false);

  const close = async () => {
    if (closing) return;
    setClosing(true);
    try {
      await apiPost(`/thoughts/${entry.id}/close`);
    } catch {
      setClosing(false);
      onError();
      return;
    }
    // Clôturer un souci rend la restitution « Pour toi » obsolète (via onClosed).
    setCollapsing(true);
    setTimeout(onClosed, 260);
  };

  return (
    <div
      className={`depot-entry${entry.closed ? " closed" : ""}`}
      style={collapsing ? COLLAPSE_STYLE : undefined}
    >
      <div className="depot-entry-body">
        <div className="depot-entry-content">{entry.content}</div>
        <div className="depot-entry-age">{formatRelativeAge(entry.created_at)}</div>
      </div>
      {entry.closed ? (
        <span className="depot-entry-done">réglé</span>
      ) : (
        <button className="depot-entry-btn" type="button" onClick={() => void close()}>
          C'est réglé
        </button>
      )}
    </div>
  );
}
