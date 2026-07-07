// ── Formatage FR partagé (dates, âges, montants) ─────────────────────────────
// Portage 1:1 des helpers de bot/static/js/ui.js. Toute divergence de sortie
// est un bug (migration iso-fonctionnelle). Réutilisés par les cards du
// dashboard (step 04) et les overlays à venir (steps 05/06).

/** Deux `Date` tombent-elles le même jour civil local ? */
export function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/** Heure locale « 14:05 ». */
export function formatHM(d: Date): string {
  return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

/** « Demain » sinon « lun. 7 juil. » (jour relatif court). */
export function formatRelativeDay(d: Date): string {
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  if (sameDay(d, tomorrow)) return "Demain";
  return d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" });
}

/** « à l'instant » / « il y a 12 min » / « il y a 3 h » / « il y a 2 j ». */
export function formatRelativeAge(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const ageMin = Math.max(0, Math.floor((Date.now() - d.getTime()) / 60_000));
  if (ageMin < 1) return "à l'instant";
  if (ageMin < 60) return `il y a ${ageMin} min`;
  const ageH = Math.floor(ageMin / 60);
  if (ageH < 24) return `il y a ${ageH} h`;
  return `il y a ${Math.floor(ageH / 24)} j`;
}

/** « lun. 7, 14:05 » — horodatage court d'une notification. */
export function formatDateTime(d: Date): string {
  return d.toLocaleString("fr-FR", { weekday: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * Évènement iCloud « journée entière » : DTSTART/DTEND alignés sur minuit
 * local, durée multiple de 24 h (1 jour = 24 h, multi-jours = 48 h, …).
 */
export function isAllDayEvent(start: Date, end: Date): boolean {
  if (start.getHours() !== 0 || start.getMinutes() !== 0 || start.getSeconds() !== 0) return false;
  if (end.getHours() !== 0 || end.getMinutes() !== 0 || end.getSeconds() !== 0) return false;
  const diffMs = end.getTime() - start.getTime();
  return diffMs > 0 && diffMs % 86_400_000 === 0;
}

/** Montant euros : entier sans décimale, sinon deux décimales à la virgule. */
export function formatEur(amount: number): string {
  const rounded = Math.round(amount * 100) / 100;
  if (Number.isInteger(rounded)) return `${rounded} €`;
  return `${rounded.toFixed(2).replace(".", ",")} €`;
}

/** Date du jour formatée pour le greeting du header (« lundi 7 juillet »). */
export function greetingDate(): string {
  return new Date().toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
}

/** Séparateur de jour du fil de discussion : « Aujourd'hui » / « Hier » / date longue. */
export function formatDaySeparator(d: Date): string {
  const today = new Date();
  if (sameDay(d, today)) return "Aujourd'hui";
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (sameDay(d, yesterday)) return "Hier";
  const opts: Intl.DateTimeFormatOptions = { weekday: "long", day: "numeric", month: "long" };
  if (d.getFullYear() !== today.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString("fr-FR", opts);
}
