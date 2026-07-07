// ── Rendu markdown ──────────────────────────────────────────────────────────
// Remplace le mini-parseur maison de bot/static/js/markdown.js par
// react-markdown + remark-gfm (parsing robuste, GFM). Le rendu reste
// STRICTEMENT iso-visuel : on n'utilise PAS la classe `prose` de
// @tailwindcss/typography (qui imposerait sa propre DA), mais les styles
// portés verbatim de components.css (`.markdown-body` plein écran, `.chat-md`
// en bulle) — cf. PROGRESS.md (stratégie CSS = règles vanilla portées).
// Réutilisé par l'actu (step 05) et par le chat (step 07).

import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// Liens ouverts hors PWA (comme le vanilla : target=_blank rel=noopener).
const COMPONENTS: Components = {
  a: ({ children, ...props }) => (
    <a {...props} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
};

interface Props {
  children: string;
  /** Classe du conteneur : `markdown-body` (plein écran) ou `chat-md` (bulle). */
  className: string;
}

export function Markdown({ children, className }: Props) {
  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
