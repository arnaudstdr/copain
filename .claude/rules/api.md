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

## Pipeline (`bot/pipeline.py`)

Transport-agnostic core. The entry point is:

```python
async def process_message(
    user_text: str,
    deps: BotDeps,
    images: list[bytes] | None = None,
) -> str: ...
```

Steps:

```python
# 1. Contextual memory (top-5 via embeddings)
memory_context = await deps.memory.retrieve_context(user_text)

# 2. Build the system prompt (memory + history)
system = build_system_prompt(memory_context, deps.history)

# 3. Single non-streamed call (LLMClient.call)
raw = await deps.llm.call(system, user_text, images=images)

# 4. Extract the <meta> block + clean text
text, meta = extract_meta(raw)

# 5. Side effects depending on the intent
await _apply_side_effects(user_text, meta, deps)
# → store memory, create task + schedule reminder (writes to pending_notifications at due time)

# 6. Branches that re-run the LLM or replace the text
if meta["intent"] == "search" and meta["search_query"]:
    results = await deps.search.search(...)
    text = await deps.llm.call_with_search(user_text, results)
elif meta["intent"] == "feed" and meta["feed"]["action"]:
    text = await _handle_feed(...)
elif meta["intent"] == "event" and meta["event"]["action"]:
    text = await _handle_event(...)
elif meta["intent"] == "fuel" and meta["fuel"]["fuel_type"]:
    text = await _handle_fuel(...)
elif meta["intent"] == "weather":
    text = await _handle_weather(...)

# 7. Rolling history + return
deps.history.extend([f"user: {user_text}", f"assistant: {text}"])
return text
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
