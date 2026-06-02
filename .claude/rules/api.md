# API layer and pipeline

## HTTP transport (`bot/api.py`)

FastAPI app served by uvicorn, three endpoints, one shared dependency for
auth. The factory `create_app(state: AppState) -> FastAPI` keeps the
construction explicit and testable: `state` is built in `bot/main.py`
before the server starts, then attached to `app.state.copain`.

```text
POST /ask         → AskResponse        # body { message }
POST /ask/stream  → StreamingResponse  # body { message } — SSE, mode dialogue PWA
POST /ask/image   → AskResponse        # body { message, image_b64, media_type }
GET  /notifications → NotificationsResponse # purges as it returns
```

Every endpoint depends on `verify_api_key` (compares the `X-API-Key`
header to `settings.api_key`). A missing/invalid key returns **403** and
logs `api_access_denied` with the source IP. There is no rate limiting at
the application layer — the bot is reachable only over Tailscale.

### LLM error handling

`POST /ask` and `POST /ask/image` catch `LLMTimeoutError` and `LLMError`
to return a friendly French message in the JSON body (status 200), so the
iOS Shortcut can display them as-is. Unexpected exceptions are logged via
`log.exception(...)` and forwarded to Sentry through `capture_exception`,
then re-raised as HTTP 500.

### Base64 images

`POST /ask/image` decodes `image_b64` with `validate=True` and returns a
400 on bad input. The bytes are passed to the pipeline as
`images=[image_bytes]`; the multimodal LLM handles caption + image in a
single call (no streaming on photos — the local fallback is text-only).

## Pipeline (`bot/pipeline/`)

Cœur transport-agnostic, organisé en package à responsabilités nettes :

```text
bot/pipeline/
├── __init__.py      # API publique : BotDeps, process_message(+_stream),
│                    #   StreamEvent, MAX_HISTORY, FALLBACK_TEXT
├── core.py          # BotDeps, StreamEvent, les deux orchestrateurs + helpers partagés
├── dates.py         # parsing de dates FR (parse_due, parse_range, parse_weather_range,
│                    #   parse_when_to_date, normalize_fr_time_words)
├── side_effects.py  # apply_side_effects (memory/task/depot/expense) + helpers finance
└── handlers.py      # run_intent_handler + handle_feed/event/fuel/weather + formateurs
```

Graphe d'imports (DAG simple) : `core → handlers + side_effects → dates`.
Les consommateurs (`bot/api.py`, `bot/main.py`, `bot/dashboard.py`, tests)
n'importent que l'API publique ré-exportée par `__init__.py` — jamais de
helper privé inter-module.

Le point d'entrée est :

```python
async def process_message(
    user_text: str,
    deps: BotDeps,
    images: list[bytes] | None = None,
    voice_mode: bool = False,
) -> tuple[str, Meta]: ...
```

La séquence métier n'est écrite qu'une fois, partagée par les deux
orchestrateurs de `core.py` via des helpers privés :

```python
# 1. System prompt complet — _build_prompt
#    (mémoire RAG top-5, history, localisation, récurrentes pending)
system_prompt = await _build_prompt(user_text, deps, voice_mode=voice_mode)

# 2. Appel LLM — SEUL point de divergence transport :
#    non-stream : llm.call (images, fallback local complet)
#    stream     : llm.chat_stream + MetaStreamFilter (texte seul)
raw = await deps.llm.call(system=system_prompt, user=user_content, images=images)

# 3. Extraction du bloc <meta> — _try_extract_meta
#    None = bloc absent/invalide → FALLBACK_TEXT + meta neutre,
#    NI side effects NI entrée dans l'history
text, meta = _try_extract_meta(raw) or fallback

# 4. Séquence métier commune — _route_and_apply
#    → side_effects.apply_side_effects (memory/task/depot/expense ;
#      les rappels écrivent dans pending_notifications à l'échéance)
#    → intent search : recherche SearXNG, l'orchestrateur produit le résumé
#      (call_with_search non-stream / call_with_search_stream streamé)
#    → autres intents : handlers.run_intent_handler (feed/event/fuel/weather)
#      peut remplacer l'intro optimiste par le texte final
outcome = await _route_and_apply(user_text, meta, deps, intro=text)

# 5. History roulante APRÈS production du texte final — _record_history
_record_history(deps, history_user, text)
return text, meta
```

## Streaming (`POST /ask/stream` + `process_message_stream`)

Le mode dialogue de la PWA consomme la variante streamée du pipeline,
`process_message_stream(user_text, deps)`, un générateur async de
`StreamEvent` sérialisés en frames SSE (`data: {json}\n\n`) :

- `delta`   — chunk de texte visible (le bloc `<meta>` est filtré au fil de
  l'eau par `MetaStreamFilter`, même coupé entre deux chunks Ollama) ;
- `replace` — le texte remplace tout ce qui est affiché (handlers Python
  feed/event/fuel/weather, fallback meta invalide, reset avant le résumé
  search) ;
- `done`    — fin de réponse, porte `intent` + `refresh_cards` ;
- `error`   — message FR convivial (le status HTTP est figé à 200 dès
  l'ouverture du stream, les erreurs LLM passent donc par une frame).

L'intent n'étant connu qu'à la fin du premier appel LLM, l'intro est
streamée de façon optimiste puis remplacée si un handler produit le texte
final. Pour `search`, le résumé du second appel LLM est lui aussi streamé
(`call_with_search_stream`). Chemin texte uniquement : les photos restent
sur `/ask/image`, Siri et la bulle éphémère du dashboard sur `/ask`
(non-streamé, fallback local complet).

## Notifications (`bot/notifications/`)

`NotificationStore` (SQLAlchemy async) owns the `pending_notifications`
table (shared `Base` with `tasks.db`):

- `add(text)` — enqueue a row (briefing, task reminder, proactivity push).
- `get_unread()` — FIFO order, only rows where `read_at IS NULL`.
- `mark_read(ids)` — stamps `read_at = utcnow()` (rows are kept for audit,
  not deleted).

`GET /notifications` reads the unread rows then immediately marks them
read in the same request. If the iOS client drops a payload, that batch
is lost; this is intentional (single-user, low stakes) — adding an ack
endpoint is straightforward if it ever matters.
