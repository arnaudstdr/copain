"""Configuration structlog : stdout (console coloré en dev, JSON en prod) + fichier rotatif JSON.

Deux handlers montés sur le root logger :

- `StreamHandler(sys.stdout)` — capté par `docker logs`, rendu console coloré
  en dev, JSON en prod (suivant `ENV`).
- `RotatingFileHandler(log_file_path)` optionnel — toujours JSON, persisté
  dans le volume Docker pour survivre aux redémarrages et être greppable
  après coup.

Le pattern `structlog.stdlib.ProcessorFormatter` permet à chaque handler
stdlib d'avoir son propre renderer final tout en partageant la même pipeline
de processors (timestamp, niveau, exc_info, contextvars).
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

LOG_FILE_MAX_BYTES = 5_000_000
LOG_FILE_BACKUP_COUNT = 5


def _shared_processors() -> list[structlog.types.Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def configure_logging(
    env: str = "dev",
    level: int = logging.INFO,
    log_file_path: Path | None = None,
) -> None:
    """Initialise structlog et redirige logging stdlib vers la même pipeline.

    Si `log_file_path` est fourni, ajoute un `RotatingFileHandler` JSON
    (5 Mo x 5 backups). Le parent du fichier est créé au besoin.
    """
    shared = _shared_processors()

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    stdout_renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer(colors=True)
        if env == "dev"
        else structlog.processors.JSONRenderer()
    )
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=stdout_renderer,
            foreign_pre_chain=shared,
        )
    )

    handlers: list[logging.Handler] = [stdout_handler]

    if log_file_path is not None:
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=log_file_path,
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=structlog.processors.JSONRenderer(),
                foreign_pre_chain=shared,
            )
        )
        handlers.append(file_handler)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(level)

    # Mute le logger ChromaDB qui loggue en erreur à chaque event télémétrique
    # alors que la télémétrie est désactivée (anonymized_telemetry=False).
    # Bug connu : chromadb 0.6.x incompatible avec posthog >= 7 (nouvelle
    # signature de `capture()`). Les logs d'erreur n'apportent rien et
    # polluent le fichier rotatif.
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

    if log_file_path is not None:
        structlog.get_logger(__name__).info(
            "logging_configured",
            file=str(log_file_path),
            max_bytes=LOG_FILE_MAX_BYTES,
            backup_count=LOG_FILE_BACKUP_COUNT,
        )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
