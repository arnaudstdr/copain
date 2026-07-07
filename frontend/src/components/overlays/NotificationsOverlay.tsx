// ── Overlay Notifications ────────────────────────────────────────────────────
// Liste GET /notifications (le GET purge côté backend : marque lues + vide la
// file). On ne fetch donc QU'UNE FOIS à l'ouverture (cf. points de vigilance du
// step). Iso-visuel de openNotifs() de bot/static/js/overlays.js.

import { useEffect, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { apiGet } from "../../api/client";
import type { NotificationsResponse, NotificationItem } from "../../api/types";
import { formatDateTime } from "../../lib/format";
import { Overlay, PanelEmpty } from "../Overlay";

interface Props {
  onClose: () => void;
  // Le GET a marqué les notifs lues côté backend → le badge doit retomber à 0.
  onRead: () => void;
}

type Status = "loading" | "error" | "ready";

export function NotificationsOverlay({ onClose, onRead }: Props) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [status, setStatus] = useState<Status>("loading");
  // Le GET /notifications PURGE côté backend : on ne doit le lancer qu'une seule
  // fois, y compris sous le double-montage de React.StrictMode en dev (sinon le
  // 2e appel lit une file déjà vidée). Ref = garde-fou d'idempotence.
  const fetched = useRef(false);

  useEffect(() => {
    if (fetched.current) return;
    fetched.current = true;
    apiGet<NotificationsResponse>("/notifications")
      .then((data) => {
        setItems(data.notifications ?? []);
        setStatus("ready");
        onRead();
      })
      .catch(() => setStatus("error"));
  }, [onRead]);

  return (
    <Overlay icon={Bell} title="Notifications" onClose={onClose}>
      <div className="panel-list">
        {status === "loading" ? (
          <PanelEmpty>Chargement…</PanelEmpty>
        ) : status === "error" ? (
          <PanelEmpty>Impossible de charger</PanelEmpty>
        ) : items.length === 0 ? (
          <PanelEmpty>Aucune notification en attente</PanelEmpty>
        ) : (
          items.map((n) => (
            <div className="notif-item" key={n.id}>
              <div className="notif-text">{n.text}</div>
              <div className="notif-time">{formatDateTime(new Date(n.created_at))}</div>
            </div>
          ))
        )}
      </div>
    </Overlay>
  );
}
