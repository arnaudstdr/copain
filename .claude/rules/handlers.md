---
paths:
  - "bot/handlers.py"
  - "bot/main.py"
---

# Handlers and message processing

## Message processing flow

```python
async def _process(user_text, chat_id, deps, images=None) -> str:
    # 1. Contextual memory (top-5 via embeddings)
    memory_context = await deps.memory.retrieve_context(user_text)

    # 2. Build the system prompt (memory + history)
    system = build_system_prompt(memory_context, deps.history)

    # 3. Call the LLM (+ optional base64 images)
    raw = await deps.llm.call(system=system, user=user_text, images=images)

    # 4. Extract the <meta> block + clean text
    text, meta = extract_meta(raw)

    # 5. Side effects depending on the intent
    await _apply_side_effects(user_text, chat_id, meta, deps)
    # → store memory, create task + reminder scheduler

    # 6. Branches that re-run the LLM or replace the text
    if meta["intent"] == "search" and meta["search_query"]:
        results = await deps.search.search(...)
        text = await deps.llm.call_with_search(user_text, results)
    elif meta["intent"] == "feed" and meta["feed"]["action"]:
        text = await _handle_feed(...)   # add/list/remove/summarize
    elif meta["intent"] == "event" and meta["event"]["action"]:
        text = await _handle_event(...)  # create (iCloud) / list
    elif meta["intent"] == "fuel" and meta["fuel"]["fuel_type"]:
        text = await _handle_fuel(...)   # fuel prices + geocoding
    elif meta["intent"] == "weather":
        text = await _handle_weather(...) # Open-Meteo + geocoding + day range

    # 7. Rolling history + return
    deps.history.extend([f"user: {user_text}", f"assistant: {text}"])
    return text
```

For photos, `make_photo_handler` downloads the Telegram blob and calls the
same `_process()` with `images=[bytes]`. The multimodal LLM handles caption
+ image in a single call.

## Global error handling

- Telegram handlers wrap each call in `try/except` and respond with a
  generic message on internal errors.
- An `Application.add_error_handler(_error_handler)` is registered to
  soft-fail on `NetworkError` / `TimedOut` (momentary dropouts to
  `api.telegram.org`), which are logged as warnings without stacktrace. Any
  other error goes through `log.exception`.
- `post_init` tolerates a failure of `ICloudCalendarClient.connect()`: it
  logs a warning and lets the bot start. The `event` intent will then return
  "calendar unavailable".
