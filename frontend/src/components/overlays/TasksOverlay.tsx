// ── Overlay Tâches ───────────────────────────────────────────────────────────
// GET /tasks → liste des tâches en cours. Cochage (POST /tasks/{id}/complete) et
// suppression par swipe gauche (DELETE /tasks/{id}) avec animation de sortie.
// Iso-visuel + iso-fonctionnel de openTasks()/makeTaskRow()/attachSwipe() de
// bot/static/js/overlays.js. Le geste tactile est porté 1:1 (seuils 40/30 px,
// ouverture bornée à -84 px) via Pointer Events (unifie tactile + souris).

import { useEffect, useRef, useState } from "react";
import { Check, ListChecks } from "lucide-react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { apiDelete, apiGet, apiPost } from "../../api/client";
import type { TasksListResponse, TaskCard } from "../../api/types";
import { Overlay, PanelEmpty } from "../Overlay";
import { useToast } from "../Toast";

interface Props {
  onClose: () => void;
}

type Status = "loading" | "error" | "ready";

const REVEAL = 84; // largeur du bouton "Supprimer" révélé par le swipe.

export function TasksOverlay({ onClose }: Props) {
  const [tasks, setTasks] = useState<TaskCard[]>([]);
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    let cancelled = false;
    apiGet<TasksListResponse>("/tasks")
      .then((data) => {
        if (cancelled) return;
        setTasks(data.tasks ?? []);
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const remove = (id: number) => setTasks((prev) => prev.filter((t) => t.id !== id));

  return (
    <Overlay icon={ListChecks} title="Tâches en cours" onClose={onClose}>
      <div className="panel-list" id="tasks-list">
        {status === "loading" ? (
          <PanelEmpty>Chargement…</PanelEmpty>
        ) : status === "error" ? (
          <PanelEmpty>Impossible de charger</PanelEmpty>
        ) : tasks.length === 0 ? (
          <PanelEmpty>Aucune tâche en cours</PanelEmpty>
        ) : (
          tasks.map((t) => <TaskRow key={t.id} task={t} onRemove={remove} />)
        )}
      </div>
    </Overlay>
  );
}

function formatTaskDue(d: Date): string {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const dayDiff = Math.floor((d.getTime() - today.getTime()) / (24 * 3600 * 1000));
  const hm = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (dayDiff < 0) {
    const ago = Math.abs(dayDiff);
    return `En retard de ${ago} jour${ago > 1 ? "s" : ""}`;
  }
  if (dayDiff === 0) return `Aujourd'hui ${hm}`;
  if (dayDiff === 1) return `Demain ${hm}`;
  return `${d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" })} ${hm}`;
}

// Styles inline de l'animation de disparition (repris verbatim du vanilla :
// opacity/max-height/padding/margin transitionnés vers 0 avant retrait du DOM).
const COLLAPSE_STYLE = {
  transition: "opacity 0.25s, max-height 0.25s, padding 0.25s, margin 0.25s",
  opacity: 0,
  maxHeight: 0,
  paddingTop: 0,
  paddingBottom: 0,
  marginTop: 0,
  marginBottom: 0,
} as const;

function TaskRow({ task, onRemove }: { task: TaskCard; onRemove: (id: number) => void }) {
  const toast = useToast();
  const innerRef = useRef<HTMLDivElement>(null);
  const gesture = useRef({ startX: 0, currentX: 0, dragging: false });
  const [swiped, setSwiped] = useState(false);
  const [busy, setBusy] = useState(false); // cochage/suppression en cours
  const [checked, setChecked] = useState(false);
  const [collapsing, setCollapsing] = useState(false);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    gesture.current = { startX: e.clientX, currentX: e.clientX, dragging: true };
    if (innerRef.current) innerRef.current.style.transition = "none";
    innerRef.current?.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (!g.dragging) return;
    g.currentX = e.clientX;
    const dx = Math.min(0, g.currentX - g.startX); // n'autorise que le swipe gauche
    const offset = swiped ? -REVEAL + dx : dx;
    if (innerRef.current) {
      innerRef.current.style.transform = `translateX(${Math.max(-REVEAL, offset)}px)`;
    }
  };
  const onPointerUp = () => {
    const g = gesture.current;
    if (!g.dragging) return;
    g.dragging = false;
    if (innerRef.current) {
      innerRef.current.style.transition = "";
      innerRef.current.style.transform = "";
    }
    const dx = g.currentX - g.startX;
    if (!swiped && dx < -40) setSwiped(true);
    else if (swiped && dx > 30) setSwiped(false);
    // sinon : snap back à l'état courant (la classe reflète déjà `swiped`).
  };

  const complete = async () => {
    if (busy) return;
    setBusy(true);
    setChecked(true);
    try {
      await apiPost(`/tasks/${task.id}/complete`);
      // Petit délai puis effondrement, comme le vanilla.
      setTimeout(() => setCollapsing(true), 200);
      setTimeout(() => onRemove(task.id), 460);
    } catch {
      setBusy(false);
      setChecked(false);
      toast("Impossible de cocher");
    }
  };

  const del = async () => {
    try {
      await apiDelete(`/tasks/${task.id}`);
      setCollapsing(true);
      setTimeout(() => onRemove(task.id), 210);
    } catch {
      toast("Impossible de supprimer");
    }
  };

  const due = task.due_at ? new Date(task.due_at) : null;
  const overdue = due ? due < new Date() : false;

  return (
    <div
      className={`task-row${swiped ? " swiped" : ""}${busy ? " task-completing" : ""}`}
      style={collapsing ? COLLAPSE_STYLE : undefined}
    >
      <div className="task-row-delete" onClick={del}>
        Supprimer
      </div>
      <div
        ref={innerRef}
        className="task-row-inner"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onClick={() => setSwiped(false)}
      >
        <div
          className={`task-check${checked ? " checked" : ""}`}
          onClick={(e) => {
            e.stopPropagation();
            void complete();
          }}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <Check size={14} strokeWidth={3} />
        </div>
        <div className="task-body">
          <div className="task-content">{task.content}</div>
          {due && <div className={`task-due${overdue ? " overdue" : ""}`}>{formatTaskDue(due)}</div>}
        </div>
      </div>
    </div>
  );
}
