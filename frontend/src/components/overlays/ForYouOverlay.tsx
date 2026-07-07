// ── Overlay « Pour toi » (restitution des dépôts) ────────────────────────────
// GET /foryou (canal PULL, mis en cache pour la session — cf. lib/foryouCache).
// Chaque item porte deux actions : « C'est réglé » (clôture chaque thought_id
// membre via POST /thoughts/{id}/close, idempotent) et « Garder » (masquage
// LOCAL, sans réseau). État apaisant quand rien à sortir (jamais une erreur).
// Iso-fonctionnel de openForYou()/renderForYou()/resolveForYou() de
// bot/static/js/overlays.js.

import { useEffect, useRef, useState } from "react";
import { Inbox } from "lucide-react";
import { apiGet, apiPost } from "../../api/client";
import type { ForYouItemResponse, ForYouResponse } from "../../api/types";
import { getForYouCache, setForYouCache } from "../../lib/foryouCache";
import { Overlay, PanelEmpty } from "../Overlay";
import { useToast } from "../Toast";

interface Props {
  onClose: () => void;
}

const COLLAPSE_STYLE = {
  transition: "opacity 0.25s, max-height 0.25s, padding 0.25s, margin 0.25s",
  opacity: 0,
  maxHeight: 0,
  paddingTop: 0,
  paddingBottom: 0,
  marginTop: 0,
  marginBottom: 0,
} as const;

type Status = "loading" | "error" | "ready";

export function ForYouOverlay({ onClose }: Props) {
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
        // On laisse le cache à null (la card reste idle, un nouveau tap réessaie).
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

  return (
    <Overlay icon={Inbox} title="Pour toi" onClose={onClose}>
      <div className="panel-list" id="foryou-list">
        {status === "loading" ? (
          <PanelEmpty>Chargement…</PanelEmpty>
        ) : status === "error" ? (
          <PanelEmpty>Impossible de charger</PanelEmpty>
        ) : items.length === 0 ? (
          <PanelEmpty>Rien en attente — tout est rangé.</PanelEmpty>
        ) : (
          items.map((item, idx) => (
            <ForYouRow
              key={idx}
              item={item}
              onRemove={() => removeItem(item)}
              onPartialError={() => toast("Certains dépôts n'ont pas pu être clôturés")}
            />
          ))
        )}
      </div>
    </Overlay>
  );
}

function ForYouRow({
  item,
  onRemove,
  onPartialError,
}: {
  item: ForYouItemResponse;
  onRemove: () => void;
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
    collapseThenRemove();
  };

  return (
    <div
      className={`foryou-item${resolving ? " foryou-resolving" : ""}`}
      style={collapsing ? COLLAPSE_STYLE : undefined}
    >
      <div className="foryou-message">{item.message}</div>
      <div className="foryou-actions">
        <button className="foryou-btn primary" type="button" onClick={() => void resolve()}>
          C'est réglé
        </button>
        <button className="foryou-btn ghost" type="button" onClick={collapseThenRemove}>
          Garder
        </button>
      </div>
    </div>
  );
}
