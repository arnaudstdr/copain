---
paths:
  - "bot/llm/**"
---

# LLM module

## Models

| Model              | Type       | Where it runs   | Usage                            |
| ------------------ | ---------- | --------------- | -------------------------------- |
| `gemma4:31b-cloud` | Vision LLM | Ollama Cloud    | All replies + image analysis     |
| `gemma3:4b` (opt.) | Text LLM   | Ollama local Pi | Fallback when cloud fails/times out |
| `nomic-embed-text` | Embeddings | Ollama local Pi | Vectors for ChromaDB (on demand) |

The main LLM was moved to the cloud because local inference on the Pi 5 was
too slow. A smaller local model can be configured as a **fallback** to keep
the bot usable when Ollama Cloud is unreachable (see Client section below).
The fallback is text-only: images are never routed to it.

## System prompt

The full template lives in `bot/llm/prompt.py` (`SYSTEM_PROMPT_TEMPLATE`). It
contains 8 few-shot examples to stabilise gemma4's routing (feed, event,
fuel) and the `<meta>` JSON block description (see root `CLAUDE.md` for the
full schema).

Two critical rules to remember when editing the prompt:

- **Task vs event distinction**: appointment/meeting WITH a time → `event`,
  otherwise → `task`.
- **Temporal words**: the LLM copies the literal words `midi` / `minuit`
  into `start_str`; normalisation on the code side
  (`_normalize_fr_time_words`) converts them to `12:00` / `00:00` before
  `dateparser.parse`. Do not try to normalise inside the prompt.

## Client

`LLMClient` exposes four entry points:

- `call(system, user, images?)` — main chat call, supports multimodal images
  passed as base64.
- `call_with_search(message, results)` — re-runs the LLM with SearXNG
  results as context for the `search` intent branch. Passes `cacheable=True`
  because this path has no `<meta>` and no side effects.
- `chat(messages, cacheable=False)` — low-level Ollama call used by the two
  above. `cacheable=True` enables response caching (key = hash of model +
  messages); NEVER enable it on a call that carries a `<meta>` block or
  triggers side effects (memory store, task/event creation).
- `chat_stream(messages, cacheable=False)` — same as `chat` but yields text
  chunks as Ollama streams them. Used by `handlers._process` when a
  `TelegramStreamSink` is provided to progressively edit the Telegram
  message. If the primary model fails **before** the first chunk, the
  client falls back to a single non-streamed call on the fallback endpoint
  (if configured).

### Cache

`LLMClient` owns a `TTLCache` (module `bot/cache.py`) sized via
`CACHE_LLM_TTL_SEC` / `CACHE_LLM_MAX_SIZE`. Fallback responses are **never
cached**: we want the next call to retry the primary, in case the cloud is
back. Passing `cache_ttl_sec=None` at construction disables caching (used
in tests).

### Fallback

If `OLLAMA_FALLBACK_MODEL` is set, `LLMClient` builds a second Ollama
endpoint (same base URL by default, overridable via
`OLLAMA_FALLBACK_BASE_URL`). On `LLMTimeoutError` / `LLMError` from the
primary:

- If the request carries `images`, no fallback is attempted (local model is
  text-only) — the primary error is re-raised.
- Otherwise the client tries the fallback (non-streamed, even if the caller
  asked for streaming — streaming-mid-reply retries would be confusing).
- If the fallback also fails, the **primary** exception type is re-raised
  so that handler-level UX messages (`LLMTimeoutError` → "le modèle met
  trop longtemps") stay consistent.

### Streaming and the `<meta>` block

The LLM emits the `<meta>` JSON block at the **end** of its response. During
streaming, `bot.telegram_sender.visible_text(buffer)` strips any complete
`<meta>…</meta>` block, any unclosed `<meta>` still being written, and any
partial opening tag (`<m`, `<me`, `<met`, `<meta`) left at the tail of the
buffer. The full buffer (meta included) is kept for `extract_meta` at the
end, which triggers the side effects exactly as in the non-streamed path.
