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

### Ce qui a été fait

- `js/state.js` (30 l.) : config (`API_KEY`/`API_BASE`/`PROFILE_NAME`) +
  état mutable en exports nommés (bindings vivants pour les lectures) ;
  réassignations via setters `setApiKey/setLoadingFlag/setAttachment/
  setChatAttachment/setDashboardData`. `chatHistory` et `newsState` mutés
  en place → `const`, pas de setter.
- `js/ui.js` (138 l.) : `el`, `escHtml`, `LUCIDE_ICONS` (privé) +
  `lucideSvg/lucideNode/makeHead`, helpers date (`sameDay`, `formatHM`,
  `formatRelativeDay`, `formatDateTime`, `formatRelativeAge`), toast +
  éphémère. Timers toast/éphémère **internes au module** (cf. point de
  vigilance du step — pas dans state.js).
- `js/api.js` (65 l.) : `fetchConfig` (extrait du boot), `callText`,
  `callImage`, `callTextStream` (SSE) déplacés verbatim.
- `js/main.js` (50 l.) : prend le boot `DOMContentLoaded` (séquence
  conservée : appHeight → greeting → config → dashboard → setInterval)
  et `setupAppHeight` (SPEC : appHeight vit dans main.js).
- `js/legacy.js` 1 490 → 1 268 l. : imports state/ui/api, exporte
  `renderGreeting`/`loadDashboard` (pour main.js) et `renderMarkdown`
  (pour ui.js). `recognition` reste local (part dans composer.js step 05).
- `index.html` : `main.js?v=1` → `?v=2` (CSS inchangés, restent v=1).

### Écarts par rapport au plan initial

- `main.js` modifié (non listé dans « fichiers probablement impactés »
  mais imposé par le hint « séquence orchestrée par main.js ») :
  le boot + `setupAppHeight` y déménagent.
- **Cycle d'import temporaire `ui.js → legacy.js`** : `showEphemeral` a
  besoin de `renderMarkdown` qui ne sera extrait qu'au step 04. Cycle
  bénin (fonctions hoistées, appelées uniquement au runtime) ; l'import
  basculera vers `markdown.js` au step 04.
- Pas de helper générique `apiFetch` : les wrappers déplacés portent
  eux-mêmes le header X-API-Key (déplacement pur). Les fetches restants
  de legacy.js (dashboard, overlays…) migreront avec leurs fonctions aux
  steps 04-05.

### Fichiers touchés

- `bot/static/js/state.js`, `ui.js`, `api.js` (créés)
- `bot/static/js/main.js` (boot), `legacy.js` (réduction), `bot/static/index.html` (`?v=2`)

### Tests / validation

- Pas de tests JS (hors scope SPEC). Validation : diff normalisé
  ancien code vs concat des 5 modules (133 lignes d'écart, toutes =
  modifications délibérées auditées), `node --input-type=module --check`
  sur les 5 fichiers, smoke test d'import du graphe complet avec DOM
  stubé (top-level OK, `bindStaticHandlers` compris), 485 tests Python
  verts, ruff lint + format OK. Checklist manuelle ciblée avant commit.
