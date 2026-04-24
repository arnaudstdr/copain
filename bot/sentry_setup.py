"""Initialisation Sentry (erreurs + breadcrumbs des logs).

Opt-in via `SENTRY_DSN`. DSN vide = no-op (rien n'est envoyé, aucun appel
réseau). `LoggingIntegration` intercepte les logs stdlib (structlog y écrit
déjà via `ProcessorFormatter`) et convertit automatiquement les
`log.error` / `log.exception` en events Sentry, en gardant les
`log.info` / `log.warning` en breadcrumbs contextuels.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from bot.config import Settings

log = get_logger(__name__)


def configure_sentry(settings: Settings) -> bool:
    """Initialise Sentry si `SENTRY_DSN` est défini. Retourne True si activé."""
    if not settings.sentry_dsn:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment or settings.env,
        release=settings.sentry_release or None,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
    )
    log.info(
        "sentry_initialized",
        environment=settings.sentry_environment or settings.env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )
    return True


def capture_exception(exc: BaseException, **context: object) -> None:
    """Capture une exception avec du contexte additionnel (no-op si Sentry off).

    Import local pour que les tests qui ne configurent pas Sentry n'aient pas
    besoin de `sentry_sdk` en dep de test.
    """
    try:
        import sentry_sdk
    except ImportError:
        return
    with sentry_sdk.new_scope() as scope:
        for key, value in context.items():
            scope.set_extra(key, value)
        sentry_sdk.capture_exception(exc)
