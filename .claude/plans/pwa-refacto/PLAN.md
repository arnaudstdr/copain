# Plan : Refactoring de la PWA en fichiers séparés

> **Réf :** [SPEC.md](./SPEC.md)
> **Statut global :** in progress (step 01 done)

## Index des steps

| #  | Step                                              | Statut  | Dépend de | Fichier                                                            |
|----|---------------------------------------------------|---------|-----------|--------------------------------------------------------------------|
| 01 | Extraire le CSS (styles/)                         | ✅ done | —         | [steps/01-extraire-css.md](./steps/01-extraire-css.md)             |
| 02 | Basculer le JS en module ES6 (legacy.js + main.js)| ⬜ todo | 01        | [steps/02-bascule-modules.md](./steps/02-bascule-modules.md)       |
| 03 | Extraire state.js, ui.js et api.js                | ⬜ todo | 02        | [steps/03-state-ui-api.md](./steps/03-state-ui-api.md)             |
| 04 | Extraire markdown.js et dashboard.js              | ⬜ todo | 03        | [steps/04-markdown-dashboard.md](./steps/04-markdown-dashboard.md) |
| 05 | Extraire overlays.js, composer.js et chat.js      | ⬜ todo | 04        | [steps/05-overlays-composer-chat.md](./steps/05-overlays-composer-chat.md) |
| 06 | Documentation et checklist iPhone complète        | ⬜ todo | 05        | [steps/06-doc-et-checklist.md](./steps/06-doc-et-checklist.md)     |

**Légende statuts :** ⬜ todo · 🟡 in progress · ✅ done · ⏭️ skipped · ❌ blocked

## Rationale du découpage

- **CSS d'abord (step 01)** : extraction sans aucun impact JS, risque
  quasi nul, valide la mécanique de liens versionnés (`?v=N`) avant de
  toucher au code.
- **Bascule en module unique (step 02)** : le passage `<script>` inline →
  `<script type="module">` change la portée des fonctions (plus de scope
  global). C'est LE step à risque ; il se fait à code constant (tout le JS
  dans un seul `legacy.js`) pour isoler ce risque du découpage lui-même.
- **Steps 03-05** : extraction par paquets de 2-3 modules, des feuilles
  (state, ui) vers les consommateurs (chat), `legacy.js` se vidant à mesure
  jusqu'à disparaître au step 05.
- **Critère transverse** : la PWA est entièrement fonctionnelle après
  chaque step (checklist manuelle ciblée par step, complète au step 06).

## Stratégie de commit

- **Mode :** `per-step` (un commit par step)
- **Raison du choix :** chaque step laisse la PWA fonctionnelle et
  réversible isolément ; le rollback `git revert` step par step est la
  stratégie actée dans la SPEC.

## Notes globales

- **Validation manuelle uniquement** (pas de tests JS — hors scope SPEC).
  Checklist de référence : boot + greeting, cards (météo/évent/tâches/
  budget/actu), envoi /ask + bulle éphémère, streaming SSE en mode chat,
  les 4 overlays, photo, micro, swipe-to-delete tâches.
- Tester en conditions réelles sur iPhone (PWA standalone) au minimum aux
  steps 02 et 06 ; un navigateur desktop suffit pour les steps
  intermédiaires.
- Incrémenter `?v=N` dans index.html à chaque step qui modifie un asset.

---

> **À mettre à jour** : statut des steps à chaque étape de la Phase 2. Plan complet à réviser au checkpoint de replanification entre chaque step.
