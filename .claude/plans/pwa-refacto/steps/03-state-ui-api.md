# Step 03 — Extraire state.js, ui.js et api.js

> **Statut :** voir [PLAN.md](../PLAN.md) (source de vérité unique)
> **Dépend de :** 02

## Objectif

Extraire de `legacy.js` les trois modules feuilles : l'état global
(`state.js`), les helpers UI/DOM/date/Lucide (`ui.js`) et les wrappers
réseau (`api.js`).

## Critère d'acceptation

- [ ] `js/state.js` : API_KEY/API_BASE/PROFILE_NAME + état mutable
  (loading, attachment, chatAttachment, chatHistory, newsState,
  dashboardData, timers) exposés via exports + setters là où la
  réassignation l'exige
- [ ] `js/ui.js` : `el()`, `escHtml()`, dictionnaire LUCIDE_ICONS +
  `lucideSvg/lucideNode/makeHead`, helpers date (`sameDay`, `formatHM`,
  `formatRelativeDay`, `formatDateTime`, `formatRelativeAge`), toast +
  éphémère
- [ ] `js/api.js` : récupération `/config`, `callText`, `callImage`,
  `callTextStream` (parsing SSE), helpers fetch avec header X-API-Key
- [ ] `legacy.js` importe ces modules et continue de fonctionner
- [ ] Checklist ciblée OK (desktop suffit) : boot, /ask texte, /ask photo,
  un échange SSE en chat, un toast

## Fichiers probablement impactés

- `bot/static/js/{state,ui,api}.js` (création)
- `bot/static/js/legacy.js` (réduction + imports)
- `bot/static/index.html` (`?v=` incrémenté)

## Hints d'implémentation

- Les bindings d'exports ES6 sont vivants en lecture mais pas réassignables
  depuis l'extérieur : pour `loading`, `attachment`, etc., exporter soit un
  objet `state = {...}` mutable, soit des setters (`setLoading()` existe
  déjà — s'appuyer dessus).
- `callTextStream` lit le corps en `ReadableStream` et parse les frames
  `data:` — le déplacer tel quel, c'est la partie la plus sensible.
- L'API_KEY est récupérée en async au boot : conserver la séquence du
  `DOMContentLoaded` (config → dashboard), orchestrée par main.js.

## Test plan

- Manuel ciblé : boot + envoi texte + photo + un échange streamé + un toast.

## Points de vigilance

- Attention aux imports circulaires ui ↔ state (le toast lit des timers
  d'état) : si besoin, les timers du toast restent internes à ui.js.

---

## Execution notes

_(à remplir pendant le step)_
