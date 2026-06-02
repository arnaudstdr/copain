# Spec : Refactoring de la PWA (bot/static/index.html) en fichiers séparés

> **Statut :** validé
> **Auteur·rice :** Arnaud
> **Date :** 2026-06-02

## Contexte et objectif

La PWA dashboard est un **fichier unique de 1 952 lignes**
(`bot/static/index.html`) : ~290 lignes de CSS inline, ~190 lignes de HTML
et ~1 400 lignes de JS inline. Tout changement (une card, un overlay, le
parseur SSE) impose de naviguer dans un monolithe où la vue, l'état et la
logique réseau sont entremêlés.

Cartographie (audit du 2026-06-02) : le JS se découpe naturellement en
groupes déjà identifiables — boot/config, dashboard/cards, envoi `/ask` +
streaming SSE, éphémère/toast, photos/micro, 4 overlays (notifs, tâches,
météo, évents), markdown/news, mode chat, helpers UI/date/Lucide. L'état
global partagé est restreint (API_KEY, loading, attachment, chatHistory,
newsState, dashboardData).

L'objectif est un **refactoring iso-fonctionnel** : éclater le monolithe en
fichiers séparés (HTML / CSS / modules ES6) **sans changement de
comportement ni de rendu**, pour rendre la PWA maintenable.

## Périmètre

### Dans le scope

- Extraire le CSS dans des fichiers dédiés sous `bot/static/styles/`.
- Extraire le JS en **modules ES6 natifs** sous `bot/static/js/`.
- `index.html` réduit à la structure HTML + liens CSS + `<script type="module">`.
- Stratégie de cache cohérente pour les nouveaux assets (cf. Décision 4).
- Mettre à jour `CLAUDE.md` / `.claude/rules/project-structure.md`.

### Hors scope (explicitement)

- **Aucun framework ni bundler** (pas de Vite/Webpack, pas de React/Vue) —
  la PWA reste vanilla, servie telle quelle par FastAPI `StaticFiles`.
- **Service worker / mode offline** — la PWA reste "installable" mais
  online-only, comme aujourd'hui.
- **Tests JS automatisés** (Vitest/Playwright) — souhaitables mais chantier
  séparé ; le refacto est validé par test manuel sur iPhone (checklist en
  Phase 2).
- **Toute évolution fonctionnelle ou visuelle** : pixel-identique.
- **Minification** : réseau Tailscale local, gain négligeable.
- **Le backend** (`bot/pipeline.py`) — plan séparé `pipeline-refacto`.

## Utilisateurs / consommateurs

- **Safari iOS** (iPhone, mode PWA standalone) : seul client réel. Les
  modules ES6 natifs sont supportés depuis iOS 11 — aucun risque de compat.
- **FastAPI** : `GET /` sert `index.html` avec `Cache-Control: no-store` ;
  `/static/*` est monté via `StaticFiles` (sans header cache particulier).

## Décisions d'architecture

### Décision 1 : modules ES6 natifs, zéro build step

- **Choix :** `<script type="module" src="/static/js/main.js">` ; les
  modules s'importent entre eux par chemins relatifs. Aucune étape de build.
- **Alternatives considérées :** bundler (Vite) — rejeté : ajoute une
  toolchain Node au projet et au déploiement Raspberry Pi pour un seul
  client sur réseau local ; conserver le JS inline découpé en plusieurs
  `<script>` classiques — rejeté : pas d'imports explicites, l'ordre de
  chargement reste implicite (friction n°2 de la cartographie).
- **Rationale :** les imports ES6 rendent le graphe de dépendances explicite
  et vérifiable, sans aucune infrastructure nouvelle.

### Décision 2 : arborescence cible

```
bot/static/
├── index.html            # structure HTML + <link> CSS + <script type="module">
├── manifest.json         # inchangé
├── styles/
│   ├── theme.css         # variables CSS + palettes dark/light
│   ├── layout.css        # app shell, header, dashboard, composer
│   ├── components.css    # cards, overlays, chat, markdown, éphémère/toast
│   └── animations.css    # keyframes
└── js/
    ├── main.js           # boot : DOMContentLoaded, config, appHeight
    ├── state.js          # état global partagé (API_KEY, loading, caches…)
    ├── api.js            # fetch wrappers : callText, callImage, stream SSE
    ├── dashboard.js      # loadDashboard + renderers de cards
    ├── overlays.js       # notifs, tâches (swipe), météo, évents
    ├── chat.js           # mode chat (feed, envoi, bulle SSE live)
    ├── composer.js       # input bar, photos, micro (SpeechRecognition)
    ├── markdown.js       # renderMarkdown + inlineMd + vue markdown
    └── ui.js             # el(), escHtml(), Lucide, helpers date, toast/éphémère
```

- **Alternatives considérées :** un fichier par card / par overlay
  (fragmentation : ~20 fichiers de 50 lignes) ; deux gros fichiers
  `app.js`/`ui.js` (ne résout pas la navigation).
- **Rationale :** ~9 modules de 100-250 lignes alignés sur les groupes
  fonctionnels déjà présents dans le code ; chaque friction identifiée
  (attachement dupliqué dashboard/chat, SSE enfoui) a un module d'accueil
  naturel (`composer.js`, `api.js`).

### Décision 3 : état global via module `state.js`, pas de refonte

- **Choix :** les variables globales actuelles déménagent dans `state.js`
  (objet exporté ou exports nommés mutables via fonctions setter). Les
  modules y accèdent par import explicite.
- **Alternatives considérées :** state management structuré
  (events/pub-sub) — sur-ingénierie pour ~8 variables ; `window.*` —
  conserve le couplage implicite qu'on cherche à éliminer.
- **Rationale :** rendre les dépendances d'état **visibles** suffit ;
  le volume d'état ne justifie rien de plus.

### Décision 4 : cache-busting par query param de version

- **Choix :** `index.html` (servi en `no-store`) référence ses assets avec
  un paramètre de version (`/static/js/main.js?v=<n>`), incrémenté à chaque
  déploiement modifiant les assets. Les imports inter-modules restent nus
  (le navigateur revalide la chaîne depuis main.js versionné).
- **Alternatives considérées :** `no-store` sur tout `/static/` — pénalise
  les icônes/fonts à chaque ouverture ; hash de contenu dans les noms de
  fichiers — demande un build step (contredit la Décision 1).
- **Rationale :** Safari iOS cache agressivement ; aujourd'hui le problème
  est évité parce que TOUT est dans index.html no-store. Le découpage
  réintroduit le risque de PWA stale — le query param le neutralise au prix
  d'une ligne à modifier par déploiement.

## Conventions à respecter

- **JS vanilla, pas de dépendance externe** (les icônes Lucide restent le
  dictionnaire SVG inline existant, déplacé dans `ui.js`).
- **Français** pour les commentaires et les textes UI, comme aujourd'hui.
- **Nommage existant conservé** (camelCase, `openX`/`closeX` pour les
  overlays, `renderX` pour les renderers) — le refacto déplace, il ne
  renomme pas (sauf collision).
- **`escHtml()` systématique** avant toute injection `innerHTML` (la
  convention sécurité existante).
- **Pas de changement de balisage HTML** : les ids/classes utilisés par le
  CSS et le JS restent identiques (diff CSS = pur déplacement).

## Cas limites identifiés

- **PWA stale post-déploiement** (cache Safari) → Décision 4 ; vérifier sur
  iPhone après le premier déploiement réel.
- **Ordre d'initialisation** : `setupAppHeight()` et la récupération de
  `/config` (API_KEY) doivent précéder le premier `loadDashboard()` — le
  boot séquencé vit dans `main.js`, seul point d'entrée.
- **`visualViewport` iOS** : le handler de hauteur d'app est sensible au
  contexte standalone — à re-tester spécifiquement (clavier ouvert/fermé).
- **SpeechRecognition** : API préfixée Safari (`webkitSpeechRecognition`) —
  la détection existante part dans `composer.js` sans modification.
- **Échec de chargement d'un module** (réseau coupé au boot) : comportement
  identique à l'actuel index.html tronqué — pas de gestion ajoutée (online-
  only assumé).

## Migrations / compatibilité

- **État avant :** un seul `index.html` de 1 952 lignes.
- **Stratégie :** extraction incrémentale par responsabilité (CSS d'abord,
  puis modules JS feuilles → cœur), la PWA restant fonctionnelle après
  chaque step (test manuel : checklist boot / cards / ask / SSE / overlays /
  chat / photo / micro).
- **Rollback :** un commit par step → `git revert`. Aucune donnée impactée.

## Questions ouvertes

- [ ] (non bloquant) Introduire un smoke test Playwright minimal (boot +
  dashboard render avec API mockée) en fin de plan ? À trancher en Phase 1 —
  coût d'une dépendance dev Node à peser.

---

> Une fois ce spec validé, on peut passer en Phase 1 (génération du PLAN.md).
