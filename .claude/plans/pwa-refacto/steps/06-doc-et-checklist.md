# Step 06 — Documentation et checklist iPhone complète

> **Statut :** voir [PLAN.md](../PLAN.md) (source de vérité unique)
> **Dépend de :** 05

## Objectif

Valider le refacto de bout en bout sur l'appareil réel et synchroniser la
documentation.

## Critère d'acceptation

- [ ] Checklist complète **sur iPhone en PWA standalone** : boot + greeting,
  5 cards, /ask texte + bulle éphémère, photo, micro, mode chat avec
  streaming SSE, 4 overlays, swipe-to-delete, export CSV budget, thèmes
  clair/sombre, clavier ouvert/fermé (visualViewport)
- [ ] Test du cache-busting : déploiement réel, vérifier que la PWA charge
  la nouvelle version après incrément de `?v=` (sans vider le cache à la
  main)
- [ ] `CLAUDE.md` : ligne « Interface web » et description PWA à jour
- [ ] `.claude/rules/project-structure.md` : arborescence `bot/static/` à
  jour
- [ ] Aucun fichier JS ne dépasse ~300 lignes (`wc -l bot/static/js/*.js`)

## Fichiers probablement impactés

- `CLAUDE.md`
- `.claude/rules/project-structure.md`

## Hints d'implémentation

- Si la question ouverte de la SPEC (smoke test Playwright) est retenue,
  c'est ici qu'elle s'insère — sinon la clore explicitement dans
  PROGRESS.md (dette acceptée).

## Test plan

- La checklist iPhone EST le test plan de ce step.

## Points de vigilance

- Tester le scénario « PWA déjà installée avant le refacto » : c'est le cas
  réel de l'utilisateur (cache Safari préexistant).

---

## Execution notes

_(à remplir pendant le step)_
