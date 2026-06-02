# Step 02 — Basculer le JS en module ES6 (legacy.js + main.js)

> **Statut :** voir [PLAN.md](../PLAN.md) (source de vérité unique)
> **Dépend de :** 01

## Objectif

Sortir les ~1 400 lignes de JS inline dans `bot/static/js/legacy.js`
(contenu inchangé) importé par un `bot/static/js/main.js` minimal chargé
en `<script type="module">`. C'est la bascule de portée (scope global →
module) : le step à risque, fait à code constant.

## Critère d'acceptation

- [ ] `index.html` : un seul
  `<script type="module" src="/static/js/main.js?v=N">`
- [ ] `js/legacy.js` contient tout le JS d'origine ; `js/main.js` importe
  legacy et déclenche le boot
- [ ] **Aucun handler inline cassé** : tout attribut `onclick=`/`oninput=`/
  `onsubmit=` du HTML statique est remplacé par un `addEventListener` dans
  legacy.js (les fonctions ne sont plus dans le scope global)
- [ ] Checklist fonctionnelle complète OK **sur iPhone** (boot, cards,
  /ask, SSE chat, 4 overlays, photo, micro, swipe)

## Fichiers probablement impactés

- `bot/static/index.html` (script inline → lien module ; attributs
  on* éventuels)
- `bot/static/js/main.js`, `bot/static/js/legacy.js` (création)

## Hints d'implémentation

- Recenser d'abord les handlers inline :
  `grep -oE 'on[a-z]+="[^"]+"' bot/static/index.html | sort -u`.
- Les modules sont `defer` par nature : le `DOMContentLoaded` actuel
  fonctionne toujours, mais peut être simplifié (le DOM est déjà parsé) —
  NE PAS simplifier à ce step, code constant.
- `type="module"` active le strict mode : vérifier qu'aucune variable du
  legacy n'est assignée sans déclaration (`grep` des assignations nues si
  doute, sinon la console le dira immédiatement).

## Test plan

- Manuel complet sur iPhone (PWA standalone) — c'est le step où tout peut
  casser silencieusement : tester chaque entrée de la checklist du PLAN.

## Points de vigilance

- Safari iOS + PWA standalone a son propre cache : penser à incrémenter
  `?v=` ET à tuer/relancer la PWA pour valider.
- `SpeechRecognition` (préfixe webkit) et `visualViewport` se comportent
  différemment en standalone vs onglet Safari — tester en standalone.

---

## Execution notes

- **Fait** : JS inline (lignes 227-1662 d'index.html, 1 436 lignes) extrait
  tel quel vers `bot/static/js/legacy.js` (dé-indenté de 2 espaces) ;
  `bot/static/js/main.js` créé (`import "./legacy.js"` + commentaire) ;
  `index.html` réduit à 227 lignes avec un unique
  `<script type="module" src="/static/js/main.js?v=1">` avant `</body>`.
- **Handlers inline** : 25 attributs `on*` retirés du HTML statique,
  remplacés par un bloc `bindStaticHandlers()` ajouté en fin de
  `legacy.js` (addEventListener par sélecteur ; helper `closeOnBackdrop`
  pour l'idiome `if(event.target===this)closeX()`). Les boutons sans id
  sont ciblés par sélecteur structurel (ex.
  `#bar .icon-btn[title="Joindre une photo"]`) — aucun id ajouté au HTML.
- **Écart au plan** : aucun. `?v=1` (et pas v=2) car `main.js`/`legacy.js`
  sont des assets neufs, jamais cachés par Safari.
- **Fichiers touchés** : `bot/static/index.html`,
  `bot/static/js/legacy.js` (créé), `bot/static/js/main.js` (créé).
- **Vérifications** : diff normalisé OK (legacy.js hors bloc bindings ==
  JS d'origine de git HEAD) ; syntaxe validée par `node --check` en mode
  module ; 485 tests Python verts ; ruff check + format OK.
- **Reste à valider** : checklist fonctionnelle complète sur iPhone (PWA
  standalone) — critère d'acceptation final du step.
