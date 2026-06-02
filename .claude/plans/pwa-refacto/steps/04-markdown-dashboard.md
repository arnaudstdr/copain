# Step 04 — Extraire markdown.js et dashboard.js

> **Statut :** voir [PLAN.md](../PLAN.md) (source de vérité unique)
> **Dépend de :** 03

## Objectif

Extraire le rendu markdown (+ vue markdown plein écran) et tout le
dashboard (loadDashboard + renderers de cards + news + budget).

## Critère d'acceptation

- [x] `js/markdown.js` : `renderMarkdown`, `inlineMd`,
  `openMarkdownView`, `closeMarkdownView`
- [x] `js/dashboard.js` : `loadDashboard`, `renderDashboard`, les
  renderers de cards (weather/event/tasks/budget/news), `envelopeRow`,
  `formatEur`, `openBudget`, `exportExpensesCsv`, `openNews` + newsState,
  `flashCards`, `renderBellBadge`
- [x] `legacy.js` réduit d'autant, tout fonctionne
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

### Ce qui a été fait

- `js/markdown.js` créé (122 l.) : `renderMarkdown` (export),
  `inlineMd` (interne), `openMarkdownView`, `closeMarkdownView` (exports).
  Importe `escHtml`/`lucideSvg` depuis `ui.js`.
- `js/dashboard.js` créé (408 l.) : `loadDashboard` (export),
  `renderDashboard`, `renderDashboardError`, les 5 renderers de cards,
  `appendOverdueLine`, `envelopeRow`, `formatEur`, `openBudget`,
  `exportExpensesCsv`, `renderBudgetMarkdown`, `openNews`,
  `renderBellBadge` (export), `flashCards` (export). Consomme `newsState`
  et `dashboardData` depuis `state.js` (pas de duplication).
- `legacy.js` 1 268 → 754 l. ; le cycle temporaire `ui → legacy`
  (renderMarkdown) est résorbé comme prévu.
- `main.js` importe désormais `loadDashboard` depuis `dashboard.js` ;
  `ui.js` importe `renderMarkdown` depuis `markdown.js`.
- `index.html` : `main.js?v=2` → `?v=3`.

### Écarts par rapport au plan initial

- **`isAllDayEvent` déplacé dans `ui.js`** (section helpers date, exporté) :
  utilisé à la fois par `eventCard` (dashboard.js) et `makeEventItem`
  (overlay évents, encore dans legacy.js jusqu'au step 05). Le ranger dans
  les helpers date évite une duplication et n'épaissit pas le cycle
  dashboard ↔ legacy.
- **Cycle d'import temporaire `dashboard ↔ legacy`** : les cards tappables
  référencent `openWeather`/`openEvents`/`openTasks`, exportés de
  `legacy.js` en attendant `overlays.js` (step 05). Bénin (fonctions
  hoistées, appel runtime uniquement), même mécanique que l'ex-cycle
  `ui → legacy` du step 03.
- `triggerAsk` (legacy.js) identifié comme code mort (aucun appelant) —
  non supprimé (hors scope pur déplacement), à traiter au step 05.

### Fichiers touchés

- `bot/static/js/markdown.js`, `bot/static/js/dashboard.js` (créations)
- `bot/static/js/legacy.js`, `bot/static/js/ui.js`, `bot/static/js/main.js`
- `bot/static/index.html` (`?v=3`)

### Validation

- Diff normalisé avant/après : uniquement imports/exports et commentaires
  de transition — pur déplacement confirmé.
- `node --input-type=module --check` OK sur les 7 modules.
- Smoke test import (DOM stubé via Proxy) : graphe complet résolu, cycles
  inclus ; tests fonctionnels rapides de `renderMarkdown` (gras/italique/
  liste) et `isAllDayEvent` (all-day vs horaire).
- 485 tests Python verts, `ruff check` + `ruff format --check` OK.
- Checklist manuelle ciblée à faire (navigateur desktop suffisant) : rendu
  des 5 cards, tap card actu, overlay budget + export CSV, flash après /ask.
