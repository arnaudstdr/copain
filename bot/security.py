"""Middleware sécurité : mono-utilisateur via ALLOWED_USER_ID."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from telegram import Update

log = get_logger(__name__)


def is_allowed(update: Update, allowed_user_id: int) -> bool:
    """Retourne True si l'update provient de l'utilisateur autorisé.

    Tout accès refusé est loggé en warning (user_id + username) pour détection
    d'intrusion éventuelle.
    """
    user = update.effective_user
    if user is None:
        log.warning("access_denied_no_user")
        return False

    if user.id != allowed_user_id:
        log.warning(
            "access_denied",
            user_id=user.id,
            username=user.username,
            expected=allowed_user_id,
        )
        return False

    return True
