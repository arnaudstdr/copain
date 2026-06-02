# Progress : Refactoring de la PWA en fichiers séparés

> Log append-only des décisions et apprentissages trans-step.
> **Garder concis** — si une entrée ne sert qu'au step courant, elle va dans le step file, pas ici.

## Conventions établies

- **Vérification "pur déplacement"** : à chaque step d'extraction, valider
  par diff normalisé (lignes triées, espaces de tête et lignes vides
  ignorés) que la concaténation des fichiers extraits == le bloc d'origine.
- **Assets versionnés** : tout lien d'asset dans `index.html` porte `?v=N` ;
  on est à `v=1` depuis le step 01.

## Patterns et utilitaires réutilisables

- **`bindStaticHandlers()`** (fin de `legacy.js`) : centralise tous les
  listeners du HTML statique (ex-attributs `on*`). Les steps 03-05 doivent
  déplacer chaque binding vers le module qui possède le handler — ne pas
  recréer d'attributs inline ni de globals `window.*`.

## Décisions d'archi raffinées

- **État partagé (state.js)** : exports nommés à bindings vivants pour les
  lectures ; toute réassignation passe par un setter (`setLoadingFlag`,
  `setAttachment`, …). Les objets mutés en place (`chatHistory`,
  `newsState`) sont `const` sans setter. Les steps 04-05 doivent suivre ce
  pattern (jamais de réassignation directe d'un binding importé).
- **Cycle d'import temporaire `ui.js → legacy.js`** (renderMarkdown pour
  showEphemeral) : bénin (fonctions hoistées). À résorber au step 04 en le
  basculant vers `markdown.js` (cycle résiduel ui ↔ markdown accepté).
- **Cycle temporaire `dashboard ↔ legacy`** (step 04) : les cards tappables
  importent `openWeather`/`openEvents`/`openTasks` depuis `legacy.js`.
  À résorber au step 05 quand ces fonctions partent dans `overlays.js`
  (dashboard.js importera alors overlays.js, sans cycle).
- **`isAllDayEvent` vit dans `ui.js`** (helpers date, exporté) : partagé
  entre eventCard (dashboard.js) et makeEventItem (futur overlays.js).

## Dette technique acceptée

- _(vide pour l'instant)_

## Alternatives écartées (chemin faisant)

- _(vide pour l'instant)_

---

## Journal par step

> Une entrée par step terminé, ajoutée au moment du checkpoint avant `/clear`. Très brève — 2-5 bullets max.

### Step 01 — Extraire le CSS

- 4 fichiers créés sous `bot/static/styles/`, `index.html` 1 951 → 1 665 l.
- `@keyframes flash` regroupé dans `animations.css` (insensible à l'ordre).
- Éphémère/toast dans `components.css` bien qu'intercalés avec le composer
  dans le source — sélecteurs disjoints, cascade inchangée.
- Validation : diff normalisé identique + 485 tests Python verts + ruff OK.

### Step 02 — Basculer le JS en module ES6

- JS inline → `js/legacy.js` (pur déplacement vérifié) + `js/main.js`
  (point d'entrée module) ; `index.html` 1 665 → 227 lignes.
- 25 attributs `on*` remplacés par `bindStaticHandlers()` (sélecteurs
  structurels, aucun id ajouté, helper `closeOnBackdrop`).
- Assets JS neufs en `?v=1` (jamais cachés) ; CSS inchangés restent `v=1`.
- Validation : diff normalisé + `node --check` (mode module) + 485 tests
  Python verts + ruff OK. Checklist iPhone complète à faire avant commit.

### Step 03 — Extraire state.js, ui.js et api.js

- 3 modules créés (state 30 l., ui 138 l., api 65 l.) ; legacy.js
  1 490 → 1 268 l. ; main.js prend le boot + `setupAppHeight`.
- Timers toast/éphémère internes à ui.js (pas dans state.js).
- `index.html` : `main.js?v=2` (CSS inchangés à v=1).
- Validation : diff normalisé audité + smoke test import (DOM stubé) +
  `node --check` (mode module) + 485 tests Python verts + ruff OK.

### Step 04 — Extraire markdown.js et dashboard.js

- 2 modules créés (markdown 122 l., dashboard 408 l.) ; legacy.js
  1 268 → 754 l. ; cycle `ui → legacy` résorbé (ui ↔ markdown accepté).
- `isAllDayEvent` déplacé dans `ui.js` (partagé eventCard / makeEventItem).
- Cycle temporaire dashboard ↔ legacy (openWeather/openEvents/openTasks),
  à résorber au step 05 ; `triggerAsk` repéré comme code mort (step 05).
- `index.html` : `main.js?v=3`.
- Validation : diff normalisé audité + smoke test import (DOM stubé) +
  `node --check` (mode module) + 485 tests Python verts + ruff OK.

### Step 05 — Extraire overlays.js, composer.js et chat.js

- _(à remplir à la fin du step)_

### Step 06 — Documentation et checklist iPhone complète

- _(à remplir à la fin du step)_
