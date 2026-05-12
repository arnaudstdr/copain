# Apple Shortcuts pour copain

Ce guide décrit comment configurer côté iPhone deux Shortcuts qui
exploitent les endpoints du bot :

1. **Raccourci vocal "Dis à Copain"** — pour interagir avec l'assistant
   sans ouvrir la PWA, via Siri en mains-libres.
2. **Automations de localisation** — pour que le bot sache quand tu
   arrives et quand tu pars de la maison ou du bureau.

Les deux fonctionnent uniquement via Tailscale (le Pi n'est pas exposé
sur internet). Vérifie que ton iPhone est bien connecté au tailnet avant
de tester.

---

## 1. Raccourci vocal "Dis à Copain"

### Objectif

Tu dis "Dis à Copain quelle est la météo demain" → l'iPhone capte ta
voix, l'envoie au bot avec `X-Source: siri`, le bot répond en 1-2 phrases
TTS-friendly, l'iPhone lit la réponse à voix haute.

### Configuration

Ouvre l'app **Raccourcis (Shortcuts)** et crée un nouveau Shortcut avec
ces étapes (dans cet ordre) :

| # | Action | Paramètres |
|---|---|---|
| 1 | `Dictate Text` | Language : French (France). Stop Listening : When I Stop Talking |
| 2 | `Get Contents of URL` | URL : `http://<pi-tailscale-host>:8000/ask` <br> Method : `POST` <br> Headers : <br>  • `X-API-Key` = `<API_KEY du .env>` <br>  • `X-Source` = `siri` <br>  • `Content-Type` = `application/json` <br> Request Body : JSON <br>  • Key `message` = `Dictated Text` (variable de l'étape 1) |
| 3 | `Get Dictionary Value` | Get : `Value` <br> Key : `response` <br> Dictionary : `Contents of URL` |
| 4 | `Speak Text` | Text : `Dictionary Value` (étape 3) <br> Language : French (France) <br> Rate : par défaut |
| 5 | `Show Notification` *(optionnel)* | Title : `Copain` <br> Body : `Dictionary Value` |

### Phrase de déclenchement Siri

- Renomme le Shortcut en **"Dis à Copain"**.
- Tape **"Add to Siri"** → enregistre la phrase **"Dis à Copain"** (ou
  ce que tu veux comme déclencheur).
- Désormais "Hey Siri, dis à Copain ___" fonctionnera mains-libres, y
  compris depuis la Watch ou CarPlay.

### Astuce TTS

Le bot adapte automatiquement ses réponses (max 2 phrases, pas de
markdown, langage parlé) dès qu'il reçoit le header `X-Source: siri`.
Tu peux donc poser n'importe quelle question — la réponse sera
verbalisable telle quelle.

### Debug

Si la voix Siri prononce "indéfini" ou un truc bizarre :

- Vérifie que `Get Dictionary Value` pointe bien sur la clé `response`
  (pas `intent` ou `refresh_cards`).
- Lance le Shortcut depuis l'app Raccourcis (pas via Siri) pour voir
  le payload JSON renvoyé par le bot dans la step `Get Contents of URL`.

---

## 2. Automations de localisation

### Objectif

À chaque arrivée / départ d'une géofence définie côté iOS, l'iPhone
POST un event sur `/event/location`. Le bot persiste l'event en SQLite
et injecte la position courante dans le system prompt à chaque appel
LLM. Concrètement : tu pourras demander "où suis-je ?" et le bot saura
répondre, et plus tard il pourra ajuster ses réponses selon le contexte
("tu es au bureau, je décale ta tâche course de pain").

### Quatre automations à créer

Une par couple (lieu × type de transition). Ouvre l'app **Raccourcis →
onglet Automation → "+" → Create Personal Automation**.

#### Automation 1 — Arrivée à la maison

| Champ | Valeur |
|---|---|
| Trigger | `When I arrive` → Location : ta maison |
| Action 1 | `Get Contents of URL` <br> URL : `http://<pi-tailscale-host>:8000/event/location` <br> Method : POST <br> Headers : `X-API-Key`, `Content-Type: application/json` <br> Request Body (JSON) : `{"event": "arrived", "place": "home"}` |
| Run Immediately | **Oui** (désactive "Ask Before Running") |
| Notify When Run | Optionnel (à toi de voir si tu veux un retour visuel) |

#### Automation 2 — Départ de la maison

Identique à 1 mais :
- Trigger : `When I leave` → ta maison.
- Body JSON : `{"event": "left", "place": "home"}`

#### Automation 3 — Arrivée au bureau

Identique à 1 mais :
- Trigger : `When I arrive` → ton bureau (Obernai).
- Body JSON : `{"event": "arrived", "place": "work"}`

#### Automation 4 — Départ du bureau

Identique à 1 mais :
- Trigger : `When I leave` → ton bureau.
- Body JSON : `{"event": "left", "place": "work"}`

### Variantes

- **Inclure les coordonnées GPS** (utile pour le futur anti-spoof) :
  ajoute `"lat"` et `"lon"` dans le body en utilisant l'action
  `Get Current Location` puis les variables `Latitude` / `Longitude`.
- **Marquer un timestamp explicite** : si tu veux que l'event garde
  l'heure exacte de la transition iOS (et non l'heure de réception côté
  Pi), ajoute `"at"` au body avec `Current Date` formaté en ISO 8601.
- **Autres lieux** : tu peux POSTer avec n'importe quel label dans
  `place` (ex: `"sport"`, `"chez_mes_parents"`). Le bot tolère les
  labels non répertoriés et utilise la valeur brute dans le prompt.

### Vérification

Sur le Pi, après quelques transitions :

```bash
sqlite3 data/tasks.db "SELECT id, event_type, place, occurred_at FROM location_events ORDER BY id DESC LIMIT 10;"
```

Et côté usage : pose la question "où suis-je ?" dans la PWA — la réponse
doit refléter la dernière transition.

---

## Note de sécurité

Les Shortcuts envoient ton `X-API-Key` en clair sur le tailnet. Ce n'est
pas un problème dans cette config (Tailscale chiffre déjà tout le trafic
et n'est accessible que par tes appareils), mais ne partage pas les
Shortcuts exportés avec quelqu'un d'autre — la clé y est inscrite.
