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

`LLMClient` exposes five entry points:

- `call(system, user, images?)` — main chat call, supports multimodal images
  passed as base64.
- `call_with_search(message, results)` — re-runs the LLM with SearXNG
  results as context for the `search` intent branch. Passes `cacheable=True`
  because this path has no `<meta>` and no side effects.
- `call_with_search_stream(message, results)` — streamed variant of the
  above (same prompts, hence same cache key), used by the `search` branch
  of `process_message_stream` (`POST /ask/stream`).
- `chat(messages, cacheable=False)` — low-level Ollama call used by the
  above. `cacheable=True` enables response caching (key = hash of model +
  messages); NEVER enable it on a call that carries a `<meta>` block or
  triggers side effects (memory store, task/event creation).
- `chat_stream(messages, cacheable=False)` — same as `chat` but yields text
  chunks as Ollama streams them. Used by `process_message_stream` to feed
  `POST /ask/stream` (PWA dialogue mode). If the primary model fails
  **before** the first chunk, the client falls back to a single
  non-streamed call on the fallback endpoint (if configured).

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

### Reasoning (thinking)

`gemma4:31b-cloud` déclare la capacité `thinking` (Ollama). Elle est **opt-in**
via `OLLAMA_THINK` (défaut `false`), câblée jusqu'au paramètre `think` de
`LLMClient` puis aux appels `chat`/`chat_stream` du **modèle principal
uniquement** (le fallback local reste toujours `think=False`). Quand elle est
active, Ollama renvoie le raisonnement dans un champ **séparé**
`message.thinking` : `content` (donc le bloc `<meta>`) reste propre. Le code ne
lit que `content` — le thinking est ignoré côté texte et seulement tracé en
`log.debug("ollama_thinking", …)`. Contrepartie : tokens/latence en plus à
chaque appel principal.

**Override par requête** : `chat_stream(messages, think=…)` accepte un `think`
optionnel (`None` = défaut `OLLAMA_THINK`) qui prime sur `self._think` le temps
d'un appel. C'est le levier du **toggle « réflexion » du chat** : `AskRequest.think`
(body de `POST /ask/stream`) → `process_message_stream(think=…)` → `chat_stream`.
Seul le chemin streamé le porte ; `/ask` (Siri) et `/ask/image` gardent le défaut.
Le fallback local reste toujours `think=False`.

### The `<meta>` block

The LLM emits the `<meta>` JSON block at the **end** of its response.
`bot.llm.parser.extract_meta` splits the full reply into `(visible_text,
meta_dict)` and the pipeline routes side effects (memory store, task /
event creation, search, fuel/weather queries) from `meta_dict`.

On the streamed path (`POST /ask/stream`), `bot.llm.parser.MetaStreamFilter`
filters the block incrementally: `feed(chunk)` returns the safe-to-emit text
while holding back any suffix that could be the start of `<meta>` (the
marker can be split across two Ollama chunks) plus the whitespace before
it; once the marker is seen, nothing more is emitted. `raw` keeps the full
response so `extract_meta` can parse the meta once the stream ends.
