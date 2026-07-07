// ── Icône météo selon la description FR ──────────────────────────────────────
// Portage 1:1 de weatherIconName() de bot/static/js/overlays.js, mappé sur les
// composants lucide-react (au lieu des SVG inline du vanilla). Iso-visuel : même
// arbre de décision, mêmes icônes.

import {
  Cloud,
  CloudDrizzle,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSun,
  Snowflake,
  Sun,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export function weatherIcon(description: string | null | undefined): LucideIcon {
  const d = (description ?? "").toLowerCase();
  if (d.includes("orage")) return CloudLightning;
  if (d.includes("neige") || d.includes("verglaç")) return Snowflake;
  if (d.includes("pluie") || d.includes("averse")) return CloudRain;
  if (d.includes("bruine")) return CloudDrizzle;
  if (d.includes("brouillard")) return CloudFog;
  if (d.includes("couvert")) return Cloud;
  if (d.includes("partiel") || d.includes("plutôt dégagé")) return CloudSun;
  return Sun;
}
