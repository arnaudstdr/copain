# Roadmap — idées et pistes pour copain

Document vivant qui liste ce qui pourrait être ajouté au bot, à quel
coût, et avec quelles contraintes connues. Pas un engagement, pas un
plan : juste un backlog ordonné pour ne rien oublier.

Mis à jour au fil des sessions de travail. Quand une idée est livrée,
elle migre vers la section "Livré" en bas (avec un pointeur vers le
commit / le doc principal).

---

## Limites Apple à garder en tête

Ces deux verrous Apple impactent toutes les idées d'intégration iCloud.
Ils sont irréversibles côté compte utilisateur :

- **Apple Rappels "upgraded"** (depuis iOS 13 / macOS Catalina) — le
  nouveau format `RemindersKit` n'est plus exposé via CalDAV. Toute
  écriture depuis un client tiers (apps, serveurs, scripts) est
  impossible. Vérifié et abandonné dans la session précédente.

- **Apple Notes "upgraded"** (depuis iOS 9 / macOS El Capitan) — même
  histoire mais en pire (depuis 2015). Plus accessible via IMAP, plus
  rien de standard. CloudKit privé uniquement.

Conséquence pratique : pour pousser quoi que ce soit dans une app
native iOS depuis copain, **le seul canal viable est un Apple Shortcut
côté iPhone** qui poll un endpoint du bot et utilise une action native
(EventKit pour Calendrier, NotesKit pour Notes, RemindersKit pour
Rappels). C'est le pattern utilisé pour la voix Siri et la
localisation, et ce serait le pattern pour toute nouvelle intégration.

---

## Idées en attente

### Notes / mémo

Trois approches possibles, par ordre de simplicité :

1. **Système local de notes dans la PWA** *(recommandé)*
   - Nouvelle table SQLite `notes`, endpoints `GET /notes`, `POST /notes`,
     `DELETE /notes/{id}`.
   - Card "Notes" dans le dashboard + overlay éditable (même pattern
     que l'overlay tâches).
   - Possibilité de les ingérer dans la mémoire RAG (ChromaDB) pour
     que le LLM puisse y chercher.
   - Pas de dépendance iCloud, maîtrise totale, pas de friction.

2. **Shortcut iOS poll → Apple Notes natif**
   - Bot expose `GET /notes/pending`. Shortcut récurrent crée les notes
     avec l'action native `Créer une note`.
   - One-way bot → Notes. Modifications côté iPhone ne remontent pas.
   - Plus iCloud-friendly mais demande un Shortcut récurrent et perd la
     synchronisation bidirectionnelle.

3. **Service externe CalDAV/API** (Joplin, Nextcloud Notes, Notion…)
   - Plus de dépendance externe. Pertinent si on a déjà l'un de ces
     services par ailleurs (Nextcloud sur le Pi par exemple).

### Apple Mail (IMAP)

Le compte iCloud Mail est accessible en IMAP avec l'App-Specific
Password déjà utilisé pour CalDAV. Pistes :

- **Résumé matin** des mails reçus dans la nuit, intégré au briefing
  quotidien (avec filtre sur les expéditeurs importants).
- **Détection d'urgences** : pousser une notif si un mail vient d'un
  contact prioritaire défini dans le profil.
- **Tri par sujet** : extraire automatiquement les RDV, factures,
  livraisons et créer les events / tâches associés.

Effort : moyen. Lib `imaplib` ou `aioimaplib` (async). Attention à la
volumétrie et au filtrage (un compte mail courant fait facilement
10-100 mails/jour, il faut un système de filtre solide pour ne pas
noyer le briefing).

### Apple Santé

Pas d'API serveur. Seule voie : un Shortcut iOS qui exporte
quotidiennement les métriques (sommeil, pas, fréquence cardiaque) et
POST sur un endpoint du bot. Données utilisables pour :

- **Ajustement du briefing matin** ("tu as mal dormi, journée plus
  douce").
- **Card santé** sur le dashboard.
- **Mémoire factuelle** ("Arnaud a couru 5 km hier").

Effort : moyen. Demande un Shortcut récurrent côté iPhone +
endpoint + table SQLite + UI dashboard.

### HomeKit

Le bot ne peut pas parler directement à HomeKit (API locale uniquement,
nécessite un Hub Apple sur le LAN). Mais :

- **Shortcut iOS HTTP** : un Shortcut qui s'exécute sur le Hub Home
  (HomePod / Apple TV) peut être déclenché par geofence ou tap, et
  appeler des scènes HomeKit puis poster un état au bot.
- **Cas d'usage** : "arrive à la maison → allume scène 'soirée' + POST
  /event/location → bot enregistre + déclenche briefing retour".

Effort : faible côté backend, moyen côté iOS (chaque scène = un
Shortcut). Surtout intéressant si tu utilises déjà HomeKit sérieusement.

### Photos

Beaucoup plus exotique. Pistes :

- **Tag automatique** : l'utilisateur envoie une photo via `/ask/image`,
  le LLM la classe (reçu, plante, document, voiture, lieu…) et la pousse
  dans un album iCloud Photos via Shortcut + ajout d'une entrée mémoire
  RAG ("le 12 mai 2026, photo d'un reçu Lidl à 42€").
- **OCR de reçus** : extraire le montant et la date depuis une photo de
  reçu, créer une entrée comptable.

Effort : élevé. À considérer seulement si tu veux vraiment industrialiser
la prise de notes visuelles.

### Proactivité contextuelle enrichie

L'archi `ProactivityService.on_location_event` est prête pour accueillir
plus de règles. Idées :

- **Arrivée à la maison** → "tu avais 3 courses à acheter, tu as pensé ?"
  (filtrer les tâches dont le contenu matche un pattern courses + due
  aujourd'hui).
- **Arrivée au bureau** → rappeler les tâches "at work" si on les
  taggue (nécessite un champ `location` dans la table tasks).
- **Départ de la maison le matin** → "tu pars : voici les RDV de la
  journée et la météo de Sélestat → Obernai".
- **Détection de routine cassée** → "tu n'as pas fait ton sport
  mardi/jeudi cette semaine" (croiser avec le profil yaml `routines`).

Effort : faible à moyen selon la règle. Toutes utilisent les briques
déjà en place.

### Améliorations dashboard

- **Card "Tu es à…"** : afficher la localisation courante avec heure
  d'arrivée (déjà disponible dans `LocationEventStore.get_current_location()`).
- **Vue Journal** rétroactive : historique des derniers échanges, des
  events de localisation, des notifs envoyées. Pour relire ce qu'on a
  dit / fait. Possible via un nouvel endpoint `GET /journal?days=N`.
- **Ajout de tâche depuis la PWA** sans passer par le LLM : bouton "+"
  dans l'overlay tâches, formulaire minimal (contenu + due_str).
- **Édition d'une tâche** existante (renommage, changement de due_at)
  via long-press dans l'overlay.
- **Card carburant active** au lieu du raccourci textuel : montrer le
  prix le moins cher autour de `HOME_CITY` directement sur le
  dashboard. Coût : ajouter `fuel` au snapshot `/dashboard`.

### Qualité / robustesse

- **Live reload du profil YAML** : `watchfiles` détecte les changements
  de `data/profile.yaml` et recharge à chaud. Évite un restart du
  container quand on édite le profil.
- **Anti-spoof localisation** : vérifier que `lat`/`lon` reçus dans
  `/event/location` sont proches (rayon configurable) des `HOME_LAT/LON`
  ou `WORK_LAT/LON` selon le `place` annoncé. Reject si trop loin.
- **Rate limiting** sur l'API : éviter qu'une boucle bug côté Shortcut
  ne fasse exploser les coûts LLM. FastAPI a des middlewares simples.
- **Migration vers `caldav.search`** côté reminders aussi (si on
  refait du CalDAV pour autre chose). Déjà fait pour le calendrier.
- **Suppression du flag `proactivity_enabled` global** : passer en
  flags par règle (`PROACTIVITY_RAIN`, `PROACTIVITY_EVENT`,
  `PROACTIVITY_LOCATION_RETURN`). Plus granulaire.

### Long terme / exotique

- **Multi-utilisateur léger** : pour partager copain avec ma compagne
  Anne. Demande un identifiant utilisateur dans chaque endpoint + une
  table de profils + isolation des mémoires RAG. Gros refactor.
- **Voice 2-way** : au lieu du Shortcut qui appelle `/ask` puis `Speak
  Text`, ouvrir une session SSE vocale (transcription streaming +
  TTS streaming). Vraiment exotique, peu d'intérêt si Siri marche
  bien.
- **Briefing audio le matin** : générer un MP3 du briefing (TTS
  serveur) que je peux lancer dans CarPlay le matin. Demande un
  service TTS local ou OpenAI/Google.

---

## Livré (rappel)

Chaque entrée pointe vers un commit ou doc de référence.

- **PWA dashboard** — cards (météo, prochain évent, tâches, notifs,
  briefing) + input minimaliste + mode chat optionnel. (Phase 1)
- **Profil utilisateur YAML** — `data/profile.yaml` injecté dans le
  system prompt. (Phase 1)
- **Voix Siri** — header `X-Source: siri` sur `/ask` active un préambule
  TTS-friendly. Voir `docs/ios-shortcuts.md`. (Phase 2)
- **Localisation iPhone** — `POST /event/location`, géofences iOS
  Shortcuts. Voir `docs/ios-shortcuts.md`. (Phase 2)
- **Météo contextualisée** — la card météo bascule entre `HOME_*` et
  `WORK_*` selon la localisation courante. (Phase 3)
- **Détection de chevauchement** d'évents calendrier — warning textuel
  ajouté à la réponse, l'évent est créé quand même. (Phase 3)
- **Proactivité event-driven** — `ProactivityService.on_location_event`,
  règle "briefing retour" au départ du bureau ≥17h. (Phase 3)
- **Overlay tâches interactif** dans la PWA — cocher + swipe-to-delete.
  Endpoints `GET /tasks`, `POST /tasks/{id}/complete`, `DELETE /tasks/{id}`.
  (Phase 4)

## Décisions abandonnées (pour mémoire)

- **Mirror Apple Rappels via CalDAV** — implémenté, testé, supprimé une
  fois constaté qu'iCloud Reminders upgraded est inaccessible via CalDAV
  (commits `revert(reminders)`). Si un jour Apple revient sur cette
  décision (peu probable), le code git est consultable dans l'historique.
- **Création automatique de listes CalDAV** — `principal.make_calendar`
  crée des collections "orphelines" non visibles côté Rappels.app sur
  les comptes upgraded. Désactivé en faveur d'un fuzzy match sur les
  listes existantes uniquement.
