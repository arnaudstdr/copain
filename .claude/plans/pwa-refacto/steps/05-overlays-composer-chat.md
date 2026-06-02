# Step 05 — Extraire overlays.js, composer.js et chat.js

> **Statut :** voir [PLAN.md](../PLAN.md) (source de vérité unique)
> **Dépend de :** 04

## Objectif

Extraire les trois derniers blocs (overlays notifs/tâches/météo/évents,
composer input/photo/micro, mode chat) ; `legacy.js` disparaît.

## Critère d'acceptation

- [ ] `js/overlays.js` : open/close + renderers des 4 overlays, swipe
  (`attachSwipe`), `completeTask`/`deleteTask`, `formatTaskDue`,
  `weatherIconName`, `isAllDayEvent`
- [ ] `js/composer.js` : `triggerAsk`/`send`/`handleAskResponse`,
  `handleFileChange`/`removeAttachment`, micro (SpeechRecognition),
  `setLoading`/`canSend`/`updateSendBtn`/`handleKey`/`autoResize`
- [ ] `js/chat.js` : open/close chat, `renderChatFeed`/`makeChatRow`,
  pièce jointe chat, `chatSend` (SSE live bubble)
- [ ] **`legacy.js` supprimé** ; `main.js` importe et câble tous les
  modules
- [ ] Checklist ciblée OK : 4 overlays (dont swipe-to-delete), envoi
  texte/photo/micro, mode chat complet avec streaming

## Fichiers probablement impactés

- `bot/static/js/{overlays,composer,chat}.js` (création)
- `bot/static/js/legacy.js` (suppression)
- `bot/static/js/main.js` (câblage final des listeners)
- `bot/static/index.html` (`?v=` incrémenté)

## Hints d'implémentation

- C'est le step où la friction « attachement dupliqué » (attachment vs
  chatAttachment) devient visible : les DEUX restent (iso-fonctionnel),
  mais composer.js et chat.js peuvent partager un helper de lecture de
  fichier si le déplacement le rend naturel — pas plus.
- `attachSwipe` (touch + mouse) est autonome — déplacement direct.
- Les listeners du boot (boutons header, composer, chat) migrent dans
  main.js ou dans le module propriétaire du DOM concerné — choisir UNE
  convention et la noter dans PROGRESS.md.

## Test plan

- Manuel ciblé : 4 overlays + swipe + micro + photo + chat streamé.

## Points de vigilance

- Le micro partage `recognition` entre dashboard et chat — l'état vit dans
  state.js, les deux toggles dans composer.js.

---

## Execution notes

**Fait :**

- `js/overlays.js` (361 l.) : les 4 overlays (notifs, tâches, météo, évents)
  avec renderers, `attachSwipe`, `completeTask`/`deleteTask`,
  `formatTaskDue`, `weatherIconName`. `isAllDayEvent` reste dans `ui.js`
  (déjà déplacé au step 04, partagé avec dashboard.js) — importé.
- `js/composer.js` (146 l.) : `send`/`handleAskResponse`/`actionToast`,
  photo (`handleFileChange`/`removeAttachment`), micro (les deux toggles +
  `_toggleMic`), état des boutons d'envoi (`setLoading`, `canSend`,
  `canChatSend`, `updateSendBtn`, `updateChatSendBtn`, `handleKey`,
  `autoResize`).
- `js/chat.js` (181 l.) : open/close, `renderChatFeed`/`makeChatRow`,
  pièce jointe chat, `chatSend` (SSE live bubble), `handleChatKey`.
  Importe `setLoading`/`autoResize`/`updateChatSendBtn` depuis composer.js
  (sens unique, pas de cycle).
- `js/main.js` (123 l.) : récupère `renderGreeting` (boot) et
  `bindStaticHandlers` (câblage du DOM statique, déplacé tel quel avec
  imports explicites). **`legacy.js` supprimé.**
- `dashboard.js` : import `openWeather`/`openEvents`/`openTasks` basculé
  de legacy.js vers overlays.js.
- `index.html` : `main.js?v=4`.

**Écarts par rapport au plan du step :**

- `triggerAsk` **supprimé** (au lieu d'être déplacé) : code mort confirmé
  (aucun appelant), repéré au step 04.
- `recognition` reste **local à composer.js** (le step file suggérait
  state.js) : les deux toggles micro vivant dans composer.js, aucun autre
  module n'y accède — state.js reste réservé à l'état multi-modules.
- Le cycle dashboard ↔ overlays **subsiste** (le PROGRESS du step 04
  prévoyait « sans cycle ») : overlays.js a besoin de
  `loadDashboard`/`renderBellBadge`. Même nature bénigne que ui ↔ markdown
  (fonctions hoistées, appels au runtime).

**Fichiers touchés :** `bot/static/js/{overlays,composer,chat}.js` (créés),
`bot/static/js/legacy.js` (supprimé), `bot/static/js/main.js` (réécrit),
`bot/static/js/dashboard.js` (import), `bot/static/index.html` (`?v=4`).

**Validation :** diff normalisé (origine legacy+main vs nouveaux modules :
seules les 5 lignes de triggerAsk diffèrent) + `node --check` mode module
sur les 9 fichiers + smoke test import du graphe complet (DOM stubé,
`bindStaticHandlers` exécuté) + 485 tests Python verts + ruff check/format
OK. Checklist manuelle ciblée à dérouler (4 overlays + swipe + micro +
photo + chat streamé).
