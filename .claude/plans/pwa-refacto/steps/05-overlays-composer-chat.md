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

_(à remplir pendant le step)_
