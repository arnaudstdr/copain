// ── Primitives de card « Liquid Glass » ──────────────────────────────────────
// Shell + entête réutilisés par toutes les cards du dashboard. Le style vit
// dans index.css (classes .card / .card-head / .card-icon / .card-label,
// portées verbatim de bot/static/styles/components.css) : iso-visuel garanti.

import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

interface CardHeadProps {
  icon: LucideIcon;
  label: string;
}

/** Entête d'une card : pastille d'icône + kicker mono en capitales. */
export function CardHead({ icon: Icon, label }: CardHeadProps) {
  return (
    <div className="card-head">
      <div className="card-icon">
        <Icon size={16} />
      </div>
      <div className="card-label">{label}</div>
    </div>
  );
}

interface CardProps {
  children: ReactNode;
  compact?: boolean;
  empty?: boolean;
  tappable?: boolean;
  onClick?: () => void;
  className?: string;
}

/** Surface de verre. `tappable` ajoute le curseur + l'effet d'appui. */
export function Card({ children, compact, empty, tappable, onClick, className }: CardProps) {
  const cls = ["card", compact && "compact", empty && "empty", tappable && "tappable", className]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={cls} onClick={onClick}>
      {children}
    </div>
  );
}
