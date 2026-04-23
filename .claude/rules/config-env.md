---
paths:
  - "bot/config.py"
  - ".env*"
  - "docker-compose.yml"
  - "Dockerfile"
---

# Environment, configuration and deployment

## Environment variables (.env)

```env
# Telegram
TELEGRAM_BOT_TOKEN=                  # required
ALLOWED_USER_ID=                     # required, your Telegram user_id only

# Ollama (client + models)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_LLM_MODEL=gemma4:31b-cloud
OLLAMA_EMBED_MODEL=nomic-embed-text

# SearXNG (local instance)
SEARXNG_BASE_URL=http://localhost:8888

# Data paths (mounted as a Docker volume)
DATA_DIR=./data
CHROMA_DIR=./data/chroma
DB_PATH=./data/tasks.db
SCHEDULER_DB_PATH=./data/scheduler.db

# Timezone (dateparser + APScheduler + display)
TZ=Europe/Paris

# Scheduled morning briefing
BRIEFING_HOUR=8
BRIEFING_MINUTE=0

# Coordinates for weather (Open-Meteo, no API key)
HOME_LAT=48.26
HOME_LON=7.45
HOME_CITY=Sélestat

# iCloud Calendar (CalDAV) — App-Specific Password required (Apple ID 2FA)
# Generate at: https://appleid.apple.com → Sign-In and Security
ICLOUD_USERNAME=                     # required, Apple ID
ICLOUD_APP_PASSWORD=                 # required, format xxxx-xxxx-xxxx-xxxx
ICLOUD_CALENDAR_NAME=Personnel       # default calendar name (fuzzy match)

# Proactivity (notifications pushed without a request — strict opt-in)
PROACTIVITY_ENABLED=false
PROACTIVITY_WINDOW_START_HOUR=8
PROACTIVITY_WINDOW_END_HOUR=21
PROACTIVITY_DAILY_BUDGET=3
PROACTIVITY_CHECK_INTERVAL_MIN=30
PROACTIVITY_RAIN_COOLDOWN_HOURS=3

# Fuel prices (data.economie.gouv.fr — no API key)
FUEL_DEFAULT_RADIUS_KM=10              # default search radius
# User-Agent required by the Nominatim policy:
# https://operations.osmfoundation.org/policies/nominatim/
NOMINATIM_USER_AGENT=copain-bot/1.0 (personal assistant)

# Logs — rotating JSON file persisted in the Docker volume (5 MB x 5 backups).
# Leave empty to disable file persistence (stdout stays active).
LOG_FILE_PATH=./data/logs/bot.log

# Environment (dev | prod) — controls the structlog log format
ENV=dev
```

`bot/config.py` loads and validates these variables. Variables marked as
"required" crash the startup if missing (explicit `ConfigError`).

## Docker Compose

```yaml
services:
  bot:
    build: .
    restart: unless-stopped
    env_file: .env
    network_mode: host          # direct access to local Ollama on localhost
    volumes:
      - ./data:/app/data

  searxng:
    image: searxng/searxng:latest
    restart: unless-stopped
    ports:
      - "8888:8080"
    volumes:
      - ./searxng:/etc/searxng
```

Ollama runs **outside Docker** on the Pi (GPU/NPU ARM access); it targets
the `cloud` model when the model ID contains the `-cloud` suffix.

## Hardware constraints (Pi 5 8 GB)

With the LLM in the cloud, local RAM pressure is low:

| Component             | RAM                  |
| --------------------- | -------------------- |
| ChromaDB + Python bot | ~1 GB                |
| SearXNG               | ~0.3 GB              |
| nomic-embed-text      | ~0.3 GB on demand    |
| **Total**             | **~1.6 GB out of 7** |

Plenty of headroom to add other services (Home Assistant, RSSHub, etc.) if
needed in the future.
