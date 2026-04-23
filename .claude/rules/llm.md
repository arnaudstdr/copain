---
paths:
  - "bot/llm/**"
---

# LLM module

## Models

| Model              | Type       | Where it runs   | Usage                            |
| ------------------ | ---------- | --------------- | -------------------------------- |
| `gemma4:31b-cloud` | Vision LLM | Ollama Cloud    | All replies + image analysis     |
| `nomic-embed-text` | Embeddings | Ollama local Pi | Vectors for ChromaDB (on demand) |

The LLM was moved to the cloud because local inference on the Pi 5 was too
slow. As a result, most of the initial RAM budget is freed. Only
`nomic-embed-text` (~300 MB on demand), ChromaDB/bot (~1 GB), and SearXNG
(~0.3 GB) run locally on the Pi.

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

`LLMClient` exposes three entry points:

- `call(system, user, images?)` — main chat call, supports multimodal images
  passed as base64.
- `call_with_search(message, results)` — re-runs the LLM with SearXNG
  results as context for the `search` intent branch.
- `chat(messages)` — low-level Ollama call used by the two above.
