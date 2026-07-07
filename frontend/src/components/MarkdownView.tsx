// ── Vue markdown plein écran (#markdown-view) ────────────────────────────────
// Portage de openMarkdownView() de bot/static/js/markdown.js : entête (retour +
// titre/sous-titre + bouton d'action optionnel) et corps markdown scrollable.
// Réutilisée par l'actu (step 05) ; le bouton d'action servira l'export CSV
// depuis le récap Budget (step 06). Iso-visuel (#markdown-view + .markdown-body).

import { ChevronLeft } from "lucide-react";
import { Markdown } from "./Markdown";

interface Action {
  label: string;
  onClick: () => void;
}

interface Props {
  title: string;
  // Timestamp ISO (formaté en date longue) ou texte libre ; masqué si absent.
  subtitle?: string;
  markdown: string;
  onClose: () => void;
  action?: Action;
}

// Sous-titre : un ISO valide devient « lundi 7 juillet », sinon texte tel quel.
function formatSubtitle(subtitle: string): string {
  const d = new Date(subtitle);
  if (Number.isNaN(d.getTime())) return subtitle;
  return d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
}

export function MarkdownView({ title, subtitle, markdown, onClose, action }: Props) {
  return (
    <div id="markdown-view">
      <header>
        <button className="header-btn" title="Retour" type="button" onClick={onClose}>
          <ChevronLeft size={18} />
        </button>
        <div className="greeting" style={{ marginLeft: 8 }}>
          <div className="greeting-name">{title}</div>
          {subtitle && <div className="greeting-date">{formatSubtitle(subtitle)}</div>}
        </div>
        {action && (
          <button className="header-action-btn" type="button" onClick={action.onClick}>
            {action.label}
          </button>
        )}
      </header>
      <Markdown className="markdown-body">{markdown}</Markdown>
    </div>
  );
}
