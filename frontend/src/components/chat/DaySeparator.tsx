// ── Séparateur de jour du fil de discussion ─────────────────────────────────
// « Aujourd'hui » / « Hier » / date longue (cf. formatDaySeparator). Portage de
// `makeDaySeparator` (bot/static/js/chat.js).

export function DaySeparator({ label }: { label: string }) {
  return (
    <div className="chat-day-sep">
      <span>{label}</span>
    </div>
  );
}
