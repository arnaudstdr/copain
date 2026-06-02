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

- _(vide pour l'instant)_

## Décisions d'archi raffinées

- _(vide pour l'instant)_

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

- _(à remplir à la fin du step)_

### Step 03 — Extraire state.js, ui.js et api.js

- _(à remplir à la fin du step)_

### Step 04 — Extraire markdown.js et dashboard.js

- _(à remplir à la fin du step)_

### Step 05 — Extraire overlays.js, composer.js et chat.js

- _(à remplir à la fin du step)_

### Step 06 — Documentation et checklist iPhone complète

- _(à remplir à la fin du step)_
