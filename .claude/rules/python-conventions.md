---
paths:
  - "bot/**/*.py"
  - "tests/**/*.py"
---

# Python code conventions

- **Python 3.12+**, strict type hints everywhere (`mypy --strict`)
- **async/await** for all I/O (Telegram, Ollama, ChromaDB, SQLite, httpx,
  caldav in `to_thread`)
- **Explicit error handling**, no `bare except`
- **Structured logs via `structlog`** (not the standard `logging`), two
  handlers: stdout (coloured console in dev, JSON in prod depending on
  `ENV`) + rotating JSON file `data/logs/bot.log` (5 MB x 5 backups) if
  `LOG_FILE_PATH` is set — persisted in the Docker volume for `grep`/`jq`
  after the fact
- **Environment variables via `python-dotenv`**, never hardcoded values
  outside of `bot/config.py`
- **Tests with pytest + pytest-asyncio** (auto mode), external dependencies
  mocked — the suite runs without real Ollama/Telegram/iCloud/SearXNG
- **Pre-commit** with `ruff check --fix`, `ruff format`, `mypy --strict` on
  `bot/`
- **Shared SQLAlchemy Base** between modules that live together in
  `tasks.db` (tasks + feeds + notification_logs) — import `Base` from
  `bot.tasks.models`

Useful commands via the `Makefile`:

```bash
make install   # creates .venv, installs dev deps, installs pre-commit hooks
make run       # runs the bot in foreground
make test      # pytest (no external I/O)
make lint      # ruff check
make format    # ruff format + --fix
make typecheck # mypy strict
make docker-build / docker-up / docker-down
```
