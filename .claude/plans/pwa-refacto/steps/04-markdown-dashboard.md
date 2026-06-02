# Step 04 — Extraire markdown.js et dashboard.js

> **Statut :** voir [PLAN.md](../PLAN.md) (source de vérité unique)
> **Dépend de :** 03

## Objectif

Extraire le rendu markdown (+ vue markdown plein écran) et tout le
dashboard (loadDashboard + renderers de cards + news + budget).

## Critère d'acceptation

- [ ] `js/markdown.js` : `renderMarkdown`, `inlineMd`,
  `openMarkdownView`, `closeMarkdownView`
- [ ] `js/dashboard.js` : `loadDashboard`, `renderDashboard`, les
  renderers de cards (weather/event/tasks/budget/news), `envelopeRow`,
  `formatEur`, `openBudget`, `exportExpensesCsv`, `openNews` + newsState,
  `flashCards`, `renderBellBadge`
- [ ] `legacy.js` réduit d'autant, tout fonctionne
- [ ] Checklist ciblée OK : rendu des 5 cards, tap card actu (fetch +
  overlay markdown), overlay budget + export CSV, flash après /ask

## Fichiers probablement impactés

- `bot/static/js/{markdown,dashboard}.js` (création)
- `bot/static/js/legacy.js` (réduction)
- `bot/static/index.html` (`?v=` incrémenté)

## Hints d'implémentation

- `renderMarkdown` est consommé par dashboard (news), chat et la bulle
  éphémère → markdown.js est une feuille importée par les trois.
- La sécurité `escHtml` avant `innerHTML` + placeholders `{{lucide:...}}`
  ([a-z-] uniquement) : déplacer sans modifier la regex.
- `refresh_cards` renvoyé par l'API pilote `flashCards` — vérifier que le
  mapping nom de card → élément DOM reste dans dashboard.js.

## Test plan

- Manuel ciblé : les 5 cards + overlay actu + overlay budget + export CSV.

## Points de vigilance

- `newsState` (cache) vit dans state.js depuis le step 03 — dashboard.js le
  consomme, ne pas le dupliquer.

---

## Execution notes

_(à remplir pendant le step)_
