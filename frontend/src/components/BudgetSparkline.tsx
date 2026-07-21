// ── Sparkline « rythme de dépense » (SVG inline maison, step 05) ─────────────
// Deux tracés partageant le même axe X = jour calendaire du cycle : la courbe
// réelle (cumul des ponctuelles perso, `spend_curve`) et la droite « rythme
// idéal » (0 au début du cycle → `spendable_eur` en fin de cycle). Le front NE
// calcule PAS le budget : il mappe des données → pixels (Décisions 2-3 du SPEC).
// Pas de lib de chart, pas d'axes, pas d'interaction, pas d'animation.

import type { SpendPoint } from "../api/types";
import { formatEur } from "../lib/format";

interface Props {
  curve: SpendPoint[];
  spendableEur: number; // cible de la droite idéale ; ≤ 0 → droite non tracée
  cycleStart: string; // ISO date (inclus)
  // Horizon fin de cycle visé par la projection (borne haute) : fin de cycle
  // fermé, ou `cycle_start + 1 mois` pour un cycle ouvert — PAS « aujourd'hui ».
  // Porte le domaine X ET l'extrémité de la droite « rythme idéal ».
  horizon: string; // ISO date
}

// viewBox nominal ; le SVG est scalé uniformément (largeur 100 %, hauteur auto)
// pour rester dans la gouttière 16px sans distordre traits ni point.
const W = 320;
const H = 72;
const PAD = 5; // marge coordonnées : évite de rogner le trait de 2px et le point

const DAY_MS = 86_400_000;
const dayIndex = (iso: string, startMs: number) =>
  Math.round((Date.parse(`${iso}T00:00:00Z`) - startMs) / DAY_MS);

export function BudgetSparkline({ curve, spendableEur, cycleStart, horizon }: Props) {
  // Rien d'écoulé (jour du salaire sans dépense) → rien à tracer (héros masqué
  // dans le même cas). Le parent peut aussi ne pas monter le composant.
  if (curve.length === 0) return null;

  const startMs = Date.parse(`${cycleStart}T00:00:00Z`);
  // Durée du cycle en jours jusqu'à l'horizon (≥ 1 pour éviter toute division
  // par zéro). C'est l'horizon — pas « aujourd'hui » — qui fixe l'échelle : la
  // droite idéale atteint `spendableEur` en FIN de cycle, pas au jour courant.
  const spanDays = Math.max(1, dayIndex(horizon, startMs));
  const drawIdeal = spendableEur > 0;

  const innerW = W - PAD * 2;
  const innerH = H - PAD * 2;
  const maxCumul = curve.reduce((m, p) => Math.max(m, p.cumulative_eur), 0);
  // Le sommet inclut la cible idéale : la droite atteint le coin haut-droit et
  // la courbe réelle se lit « sous » ou « au-dessus » du rythme. ≥ 1 → pas de /0.
  const yMax = Math.max(maxCumul, drawIdeal ? spendableEur : 0, 1);

  const xOf = (iso: string) => PAD + (dayIndex(iso, startMs) / spanDays) * innerW;
  const yOf = (eur: number) => PAD + innerH - (eur / yMax) * innerH;

  const realPts = curve.map((p) => `${xOf(p.date).toFixed(1)},${yOf(p.cumulative_eur).toFixed(1)}`).join(" ");
  const last = curve[curve.length - 1];
  const lastX = xOf(last.date);
  const lastY = yOf(last.cumulative_eur);

  // Ambre (jamais rouge) si le cumul réel dépasse la droite idéale au dernier
  // point observé — signal sobre « au-dessus du rythme », sans dramatiser.
  const idealAtLast = drawIdeal ? (dayIndex(last.date, startMs) / spanDays) * spendableEur : Infinity;
  const over = drawIdeal && last.cumulative_eur > idealAtLast;

  const label = drawIdeal
    ? `Dépensé ${formatEur(last.cumulative_eur)}, rythme idéal ${formatEur(spendableEur)} en fin de cycle`
    : `Dépensé ${formatEur(last.cumulative_eur)}`;

  return (
    <div className="budget-spark">
      <svg className="budget-spark-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
        {drawIdeal && (
          <line
            className="spark-ideal"
            x1={xOf(cycleStart)}
            y1={yOf(0)}
            x2={xOf(horizon)}
            y2={yOf(spendableEur)}
          />
        )}
        {/* points même à 1 élément : polyline invisible, le point porte le rendu */}
        <polyline className={`spark-real${over ? " is-amber" : ""}`} points={realPts} />
        <circle className={`spark-dot${over ? " is-amber" : ""}`} cx={lastX} cy={lastY} r={3.5} />
      </svg>
      <div className="spark-legend">
        <span className="spark-legend-item">
          <span className={`spark-swatch${over ? " is-amber" : ""}`} />
          dépensé
        </span>
        {drawIdeal && (
          <span className="spark-legend-item">
            <span className="spark-swatch ideal" />
            rythme idéal
          </span>
        )}
      </div>
    </div>
  );
}
