# Step 01 — Extraire le CSS (styles/)

> **Statut :** voir [PLAN.md](../PLAN.md) (source de vérité unique)
> **Dépend de :** —

## Objectif

Déplacer les ~290 lignes de CSS inline d'`index.html` vers 4 fichiers sous
`bot/static/styles/`, liés avec versionnage `?v=1`.

## Critère d'acceptation

- [ ] `styles/theme.css` (variables + palettes dark/light),
  `styles/layout.css` (app shell, header, dashboard, composer),
  `styles/components.css` (cards, overlays, chat, markdown, éphémère,
  toast), `styles/animations.css` (keyframes)
- [ ] `index.html` ne contient plus de `<style>` ; 4 `<link rel="stylesheet"
  href="/static/styles/xxx.css?v=1">` dans le même ordre que les sections
  d'origine
- [ ] Rendu strictement identique (vérif visuelle desktop : dashboard,
  un overlay, le mode chat, thèmes clair ET sombre)
- [ ] Aucun sélecteur modifié (diff = pur déplacement)

## Fichiers probablement impactés

- `bot/static/index.html` (suppression du bloc `<style>`)
- `bot/static/styles/{theme,layout,components,animations}.css` (création)

## Hints d'implémentation

- Cartographie des sections CSS (lignes approximatives d'origine, audit
  2026-06-02) : variables/palettes 29-82, header 94-107, dashboard 109-119,
  cards/budget 114-156, composer 163-197, overlays 198-254, chat 256-284,
  markdown 289-305, animations 307-314.
- L'ordre des `<link>` doit préserver l'ordre de cascade d'origine
  (theme → layout → components → animations).
- La `@media (prefers-color-scheme: light)` reste dans `theme.css` avec
  les variables qu'elle surcharge.

## Test plan

- Manuel : ouvrir `/` en desktop, vérifier dashboard + overlay tâches +
  chat, basculer le thème système clair/sombre.

## Points de vigilance

- `--app-h` est écrite par le JS (`setupAppHeight`) — c'est une variable,
  rien à changer, mais ne pas la déclarer en dur dans theme.css avec une
  autre valeur que l'actuelle.
- `StaticFiles` sert déjà `/static/*` — aucun changement backend.

---

## Execution notes

- Extraction réalisée par plages de lignes `sed` (pur déplacement mécanique,
  dés-indentation de 4 espaces), puis vérifiée par diff normalisé : la
  concaténation des 4 fichiers contient exactement les mêmes lignes CSS que
  le bloc `<style>` d'origine (espaces de tête et lignes vides ignorés).
- Découpage effectif :
  - `theme.css` (56 l.) : reset universel + `:root` palette sombre +
    `@media (prefers-color-scheme: light)`.
  - `layout.css` (59 l.) : `html/body`, `:root --app-h`, `#app`, header,
    `#dashboard`, composer-wrap, input bar, preview photo.
  - `components.css` (161 l.) : cards/budget, icônes Lucide, éphémère,
    toast, overlays (notifs/météo/évents/tâches), chat, vues markdown.
  - `animations.css` (9 l.) : tous les `@keyframes`, y compris `flash`
    (déplacé depuis la section dashboard — les keyframes sont insensibles
    à l'ordre de déclaration).
- Écart mineur vs plan : l'éphémère et le toast étaient intercalés avec le
  composer dans le source ; ils partent dans `components.css` (conformément
  à la SPEC Décision 2) alors que le composer va dans `layout.css`. Aucun
  conflit de cascade (sélecteurs disjoints).
- `index.html` : 1 951 → 1 665 lignes ; 4 `<link ... ?v=1>` dans l'ordre
  de cascade theme → layout → components → animations.
- Fichiers touchés : `bot/static/index.html`,
  `bot/static/styles/{theme,layout,components,animations}.css` (créés).
- Tests : aucun test JS (hors scope SPEC) ; suite Python complète verte
  (485 passed), ruff lint + format OK. Vérif visuelle desktop/iPhone à
  faire par l'utilisateur (checklist du step).
