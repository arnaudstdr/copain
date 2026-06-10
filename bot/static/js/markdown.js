// ── Rendu markdown : mini-parseur + vue plein écran ───────────────────────
// Cycle d'import ui ↔ markdown accepté (cf. PROGRESS.md) : showEphemeral
// (ui.js) rend du markdown, et renderMarkdown utilise escHtml/lucideSvg
// (ui.js). Bénin : fonctions hoistées, appelées uniquement au runtime.
import { escHtml, lucideSvg } from "./ui.js?v=13";

// ── Markdown view (réutilisé pour la card Actu) ────────────────────────
export function openMarkdownView(title, text, subtitle, action) {
  const view = document.getElementById("markdown-view");
  const body = document.getElementById("markdown-body");
  const titleEl = document.getElementById("markdown-title");
  const subtitleEl = document.getElementById("markdown-subtitle");
  const actionBtn = document.getElementById("markdown-action-btn");
  titleEl.textContent = title || "";
  if (subtitle) {
    // Accepte soit un timestamp ISO, soit un texte libre.
    const d = new Date(subtitle);
    subtitleEl.textContent = isNaN(d.getTime())
      ? subtitle
      : d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
  } else {
    subtitleEl.textContent = "";
  }
  // Bouton d'action contextuel (ex: "Exporter CSV" depuis la card Budget).
  if (action && action.label && typeof action.onClick === "function") {
    actionBtn.textContent = action.label;
    actionBtn.onclick = action.onClick;
    actionBtn.classList.remove("hidden");
  } else {
    actionBtn.textContent = "";
    actionBtn.onclick = null;
    actionBtn.classList.add("hidden");
  }
  body.innerHTML = renderMarkdown(text || "");
  view.classList.remove("hidden");
  body.scrollTop = 0;
}

export function closeMarkdownView() {
  document.getElementById("markdown-view").classList.add("hidden");
}

/**
 * Mini-parseur markdown utilisé par la card Actu (et plus largement
 * tout texte enrichi côté backend).
 *
 * Couvre :
 *  - `**gras**`            → <strong>
 *  - `*italique*`          → <em>
 *  - URL nue               → <a href>
 *  - lignes commençant par `- ` → groupées dans une <ul>
 *  - lignes commençant par un emoji + `*titre*` → <h3>
 *  - autres lignes non vides → <p>
 *
 * On échappe le HTML avant traitement pour éviter toute injection
 * via le contenu (URLs externes, titres d'articles, etc.).
 */
export function renderMarkdown(text) {
  const esc = escHtml(text);
  const lines = esc.split("\n");
  const out = [];
  let listBuffer = [];

  const flushList = () => {
    if (listBuffer.length === 0) return;
    out.push("<ul>" + listBuffer.map(l => `<li>${l}</li>`).join("") + "</ul>");
    listBuffer = [];
  };

  for (const raw of lines) {
    const line = inlineMd(raw);
    const stripped = raw.trim();
    if (!stripped) { flushList(); continue; }

    // Titres ATX : "# ", "## ", "### "
    const h = stripped.match(/^(#{1,3})\s+(.+)$/);
    if (h) {
      flushList();
      const level = h[1].length;
      out.push(`<h${level}>${inlineMd(h[2])}</h${level}>`);
      continue;
    }

    // Titres de section : "📰 *Actus du jour*" ou "🤖 *Actus IA …*"
    if (/^(📰|🤖|☀️|📅|✅|📋|🌤|🌧)/.test(stripped) && /\*[^*]+\*/.test(stripped)) {
      flushList();
      out.push(`<h3>${inlineMd(raw)}</h3>`);
      continue;
    }

    // Item de liste : commence par "- " ou "* " (puces LLM)
    if (/^[-*]\s+/.test(stripped)) {
      listBuffer.push(inlineMd(raw.replace(/^\s*[-*]\s+/, "")));
      continue;
    }

    // Ligne d'indentation (continuation d'un item, ex: URL sur ligne suivante)
    if (/^\s{2,}/.test(raw) && listBuffer.length > 0) {
      listBuffer[listBuffer.length - 1] += `<br>${inlineMd(raw.trim())}`;
      continue;
    }

    flushList();
    out.push(`<p>${line}</p>`);
  }
  flushList();
  // Expansion des placeholders d'icônes ({{lucide:name}}) après rendu.
  return out.join("").replace(/\{\{lucide:([a-z-]+)\}\}/g, (_m, name) => lucideSvg(name, 14, "lucide-warn"));
}

function inlineMd(s) {
  return s
    // **gras** (greedy minimal)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    // *italique* (sans capturer **)
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>")
    // URLs nues → liens (target=_blank pour ouvrir hors PWA)
    .replace(
      /(https?:\/\/[^\s<>"'()]+)/g,
      '<a href="$1" target="_blank" rel="noopener">$1</a>'
    );
}
