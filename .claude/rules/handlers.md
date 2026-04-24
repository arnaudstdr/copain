---
paths:
  - "bot/handlers.py"
  - "bot/main.py"
---

# Handlers and message processing

## Message processing flow

```python
async def _process(user_text, chat_id, deps, images=None, sink=None) -> str:
    # 1. Contextual memory (top-5 via embeddings)
    memory_context = await deps.memory.retrieve_context(user_text)

    # 2. Build the system prompt (memory + history)
    system = build_system_prompt(memory_context, deps.history)

    # 3. Call the LLM
    #    - with sink + no images → stream via chat_stream() and push chunks
    #      through sink.emit() (progressive Telegram edit)
    #    - otherwise → one-shot call (photos + branches that re-run the LLM)
    raw = await _stream_or_call(system, user_text, images, sink, deps.llm)

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

    # 7. Non-streaming branches emit their final text through sink.emit()
    #    for UX consistency; sink.finalize() renders in MarkdownV2.

    # 8. Rolling history + return
    deps.history.extend([f"user: {user_text}", f"assistant: {text}"])
    return text
```

For photos, `make_photo_handler` downloads the Telegram blob and calls the
same `_process()` with `images=[bytes]`. The multimodal LLM handles caption
+ image in a single call (no streaming on photos — the local fallback is
text-only and streaming multimodal output is not useful here).

## Streaming (TelegramStreamSink)

`make_handler` instantiates a `TelegramStreamSink` (defined in
`bot.telegram_sender`) and passes it to `_process`. The sink:

- first `emit()` → `Message.reply_text(...)` (plain text, fast);
- subsequent `emit()` calls → `Message.edit_text(...)` with a debounce
  (`min_edit_interval_sec`, default 0.8 s) and dedup on identical content;
- `finalize()` → one last `edit_text(...)` in MarkdownV2 via
  `telegramify_markdown` (fallback to plain text if Telegram rejects the
  rendering).

During streaming, `visible_text(buffer)` strips the `<meta>` block (even
mid-write) so the user never sees the routing JSON. The full buffer (meta
included) is kept for `extract_meta` at the end.

## Global error handling

- Telegram handlers wrap each call in `try/except` and respond with a
  generic message on internal errors. `LLMTimeoutError` → "le modèle met
  trop longtemps", `LLMError` → "le serveur LLM a un souci".
- `Application.add_error_handler(_error_handler)` soft-fails on
  `NetworkError` / `TimedOut` (momentary dropouts to `api.telegram.org`),
  logged as warnings without stacktrace and **not** forwarded to Sentry.
  Any other error goes through `log.exception` AND
  `sentry_setup.capture_exception(err, source="telegram_handler", update_id=...)`.
- `post_init` tolerates a failure of `ICloudCalendarClient.connect()`: it
  logs a warning and lets the bot start. The `event` intent will then return
  "calendar unavailable".
