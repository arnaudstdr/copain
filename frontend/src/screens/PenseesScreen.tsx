// ── Onglet Pensées (segmented « Pour toi / Mes dépôts ») ────────────────────
// Réunit les deux faces de la décharge cognitive (step 03) :
//  • « Pour toi » = restitution des dépôts (ex-ForYouOverlay) : canal PULL mis
//    en cache pour la session (fetch au montage du panneau, pas au boot). Deux
//    actions par item : « C'est réglé » (clôt chaque thought_id membre) et
//    « Garder » (masquage local).
//  • « Mes dépôts » = liste des dépôts filtrée par type (ex-DepotExpressOverlay,
//    partie liste), chacun clôturable. Le dépôt lui-même se fait via le FAB
//    (formulaire extrait dans components/forms/DepotForm, monté au step 05).
// Seul le panneau actif est monté (le fetch « Pour toi » suit le montage).

import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import type {
  ForYouItemResponse,
  ForYouResponse,
  ThoughtItem,
  ThoughtKind,
  ThoughtsListResponse,
} from "../api/types";
import { getForYouCache, invalidateForYou, setForYouCache } from "../lib/foryouCache";
import { formatRelativeAge } from "../lib/format";
import { useToast } from "../components/Toast";

type Segment = "foryou" | "depots";

interface Props {
  // Une clôture (dépôt / item « Pour toi ») peut concerner le dashboard → resync.
  onChanged: () => void;
  // Compteur bumpé par un dépôt via le FAB (hors écran) → recharge la liste
  // « Mes dépôts » visible si un type est sélectionné.
  reloadKey?: number;
}

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

export function PenseesScreen({ onChanged, reloadKey = 0 }: Props) {
  const [segment, setSegment] = useState<Segment>("foryou");

  return (
    <div className="screen">
      <header>
        <div className="greeting">
          <div className="greeting-name">Pensées</div>
        </div>
      </header>
      <div className="segmented-bar">
        <div className="segmented" role="tablist" aria-label="Pensées">
          <button
            type="button"
            role="tab"
            aria-selected={segment === "foryou"}
            className={`segmented-option${segment === "foryou" ? " active" : ""}`}
            onClick={() => setSegment("foryou")}
          >
            Pour toi
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={segment === "depots"}
            className={`segmented-option${segment === "depots" ? " active" : ""}`}
            onClick={() => setSegment("depots")}
          >
            Mes dépôts
          </button>
        </div>
      </div>
      <div className="screen-scroll">
        <div className="screen-body">
          {segment === "foryou" ? (
            <ForYouPanel onChanged={onChanged} />
          ) : (
            <DepotsPanel onChanged={onChanged} reloadKey={reloadKey} />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Panneau « Pour toi » (restitution) ───────────────────────────────────────

type Status = "loading" | "error" | "ready";

function ForYouPanel({ onChanged }: { onChanged: () => void }) {
  const toast = useToast();
  const [items, setItems] = useState<ForYouItemResponse[]>([]);
  const [status, setStatus] = useState<Status>("loading");
  const fetchedAt = useRef<string | null>(null);

  useEffect(() => {
    // Cache de session déjà chaud → on réaffiche sans re-solliciter le LLM.
    const cached = getForYouCache();
    if (cached.items !== null) {
      setItems(cached.items);
      fetchedAt.current = cached.fetchedAt;
      setStatus("ready");
      return;
    }
    let cancelled = false;
    apiGet<ForYouResponse>("/foryou")
      .then((data) => {
        if (cancelled) return;
        const next = data.items ?? [];
        setForYouCache(next, data.fetched_at);
        fetchedAt.current = data.fetched_at;
        setItems(next);
        setStatus("ready");
      })
      .catch(() => {
        if (cancelled) return;
        // On laisse le cache à null (un nouveau passage réessaie).
        setStatus("error");
        toast("Impossible de charger");
      });
    return () => {
      cancelled = true;
    };
  }, [toast]);

  // Retrait local + synchro du cache (« Garder » = masquage ; « C'est réglé »
  // = après clôture). Le cache reflète la liste restante pour cette session.
  const removeItem = (item: ForYouItemResponse) => {
    setItems((prev) => {
      const next = prev.filter((i) => i !== item);
      setForYouCache(next, fetchedAt.current);
      return next;
    });
  };

  if (status === "loading") return <p className="placeholder-text">Chargement…</p>;
  if (status === "error") return <p className="placeholder-text">Impossible de charger</p>;
  if (items.length === 0)
    return <div className="panel-empty">Rien en attente — tout est rangé.</div>;

  return (
    <>
      {items.map((item) => (
        <ForYouRow
          // Clé stable dérivée du contenu (les thought_ids identifient l'item) ;
          // fallback type+message si l'item ne porte aucun dépôt.
          key={
            item.thought_ids && item.thought_ids.length > 0
              ? item.thought_ids.join(",")
              : `${item.type}:${item.message}`
          }
          item={item}
          onRemove={() => removeItem(item)}
          // Une clôture réussie depuis « Pour toi » peut périmer le dashboard.
          onResolved={onChanged}
          onPartialError={() => toast("Certains dépôts n'ont pas pu être clôturés")}
        />
      ))}
    </>
  );
}

function ForYouRow({
  item,
  onRemove,
  onResolved,
  onPartialError,
}: {
  item: ForYouItemResponse;
  onRemove: () => void;
  onResolved: () => void;
  onPartialError: () => void;
}) {
  const [resolving, setResolving] = useState(false);
  const [collapsing, setCollapsing] = useState(false);

  const collapseThenRemove = () => {
    setCollapsing(true);
    setTimeout(onRemove, 260);
  };

  const resolve = async () => {
    if (resolving) return;
    setResolving(true);
    // Un item « boucle » porte plusieurs dépôts ouverts : on les clôt tous.
    // /close est idempotent → un retour après échec partiel est sans danger.
    const results = await Promise.allSettled(
      (item.thought_ids ?? []).map((id) => apiPost(`/thoughts/${id}/close`)),
    );
    if (results.some((r) => r.status === "rejected")) {
      setResolving(false);
      onPartialError();
      return;
    }
    // Clôturer un souci rend la restitution obsolète → resync du dashboard.
    onResolved();
    collapseThenRemove();
  };

  return (
    <div
      className={`foryou-item${resolving ? " foryou-resolving" : ""}`}
      style={collapsing ? COLLAPSE_STYLE : undefined}
    >
      <div className="foryou-message">{item.message}</div>
      <div className="foryou-actions">
        <button
          className="foryou-btn primary"
          type="button"
          disabled={resolving}
          onClick={() => void resolve()}
        >
          C'est réglé
        </button>
        {/* « Garder » (masquage local) désactivé tant qu'une clôture est en vol :
            sinon un masquage partirait alors que le POST /close aboutit côté serveur. */}
        <button
          className="foryou-btn ghost"
          type="button"
          disabled={resolving}
          onClick={collapseThenRemove}
        >
          Garder
        </button>
      </div>
    </div>
  );
}

// ── Panneau « Mes dépôts » (liste filtrée par type) ──────────────────────────

const KINDS: { kind: ThoughtKind; label: string }[] = [
  { kind: "worry", label: "Souci" },
  { kind: "idea", label: "Idée" },
  { kind: "note", label: "Note" },
];

type EntriesStatus = "idle" | "loading" | "error" | "ready";

function DepotsPanel({ onChanged, reloadKey }: { onChanged: () => void; reloadKey: number }) {
  const toast = useToast();
  const [selectedKind, setSelectedKind] = useState<ThoughtKind | null>(null);
  const [entries, setEntries] = useState<ThoughtItem[]>([]);
  const [entriesStatus, setEntriesStatus] = useState<EntriesStatus>("idle");

  // Jeton de séquence : chaque loadKind s'attribue un id ; on ignore la réponse
  // si un fetch plus récent est parti (anti-course) ou si le panneau est démonté.
  const reqSeq = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const loadKind = useCallback((kind: ThoughtKind) => {
    const reqId = ++reqSeq.current;
    setEntriesStatus("loading");
    apiGet<ThoughtsListResponse>(`/thoughts?kind=${encodeURIComponent(kind)}`)
      .then((data) => {
        if (!mounted.current || reqId !== reqSeq.current) return;
        setEntries(data.thoughts ?? []);
        setEntriesStatus("ready");
      })
      .catch(() => {
        if (!mounted.current || reqId !== reqSeq.current) return;
        setEntriesStatus("error");
      });
  }, []);

  // Un dépôt via le FAB (hors écran) bumpe `reloadKey` → recharge la liste du
  // type affiché pour y faire apparaître le nouveau dépôt.
  useEffect(() => {
    if (reloadKey > 0 && selectedKind) loadKind(selectedKind);
    // selectedKind volontairement hors deps : on ne recharge que sur reloadKey,
    // le changement de type passe par toggleChip.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey, loadKind]);

  // Sélection exclusive, désélectionnable : sélectionner un type charge la liste
  // des dépôts de ce type ; re-taper le même la vide.
  const toggleChip = (kind: ThoughtKind) => {
    if (selectedKind === kind) {
      // Invalide un éventuel loadKind encore en vol pour qu'il n'écrase pas l'état vidé.
      reqSeq.current++;
      setSelectedKind(null);
      setEntries([]);
      setEntriesStatus("idle");
      return;
    }
    setSelectedKind(kind);
    loadKind(kind);
  };

  const removeEntry = (id: number) => setEntries((prev) => prev.filter((e) => e.id !== id));

  return (
    <>
      <div className="depot-chips" role="group" aria-label="Filtrer par type">
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
      <div className="depot-entries">
        {entriesStatus === "idle" && (
          <div className="depot-entries-empty">Choisis un type pour voir tes dépôts.</div>
        )}
        {entriesStatus === "loading" && <div className="depot-entries-empty">Chargement…</div>}
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
                // Clôturer un souci rend la restitution « Pour toi » obsolète.
                invalidateForYou();
                onChanged();
                removeEntry(entry.id);
              }}
              onError={() => toast("Impossible de clôturer")}
            />
          ))}
      </div>
    </>
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
