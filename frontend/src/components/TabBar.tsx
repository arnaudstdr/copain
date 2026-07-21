// ── Tab bar iOS (4 onglets) ─────────────────────────────────────────────────
// Barre fixe en bas, dernier élément du gabarit. Onglet actif en accent bleu
// système. `env(safe-area-inset-bottom)` posé dès maintenant (peaufinage device
// au step 07). Navigation sans routeur : l'état vit dans `App` (décision 1 SPEC).

import { House, Wallet, MessageSquare, Lightbulb } from "lucide-react";

export type TabName = "accueil" | "budget" | "chat" | "pensees";

// Table indexée par onglet : `satisfies Record<TabName, …>` garantit à la
// compilation qu'une valeur ajoutée à `TabName` sans entrée ici (ou une entrée
// orpheline) provoque une erreur TypeScript. L'ordre d'insertion pilote le rendu.
const TABS = {
  accueil: { label: "Accueil", Icon: House },
  budget: { label: "Budget", Icon: Wallet },
  chat: { label: "Chat", Icon: MessageSquare },
  pensees: { label: "Pensées", Icon: Lightbulb },
} satisfies Record<TabName, { label: string; Icon: typeof House }>;

const TAB_ORDER = Object.keys(TABS) as TabName[];

interface Props {
  active: TabName;
  onSelect: (tab: TabName) => void;
}

export function TabBar({ active, onSelect }: Props) {
  return (
    <nav className="tabbar" aria-label="Navigation principale">
      {TAB_ORDER.map((name) => {
        const { label, Icon } = TABS[name];
        return (
          <button
            key={name}
            type="button"
            className={`tab${active === name ? " active" : ""}`}
            aria-current={active === name ? "page" : undefined}
            onClick={() => onSelect(name)}
          >
            <Icon size={22} />
            <span className="tab-label">{label}</span>
          </button>
        );
      })}
    </nav>
  );
}
