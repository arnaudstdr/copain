"""Helper unique pour envoyer un message Telegram hors du flux PTB.

Utilisé par les jobs APScheduler (rappels, briefing matinal) qui s'exécutent
en dehors du cycle `update → handler`. Lire le token via `os.environ` plutôt
que de le sérialiser dans les args du job évite qu'il se retrouve pickle
dans le jobstore SQLAlchemy (`data/scheduler.db`).
"""

from __future__ import annotations

import os

from bot.logging_conf import get_logger

log = get_logger(__name__)


class TelegramSenderError(RuntimeError):
    """Levée si le token est absent de l'environnement au moment de l'envoi."""


async def send_message(chat_id: int, text: str) -> None:
    """Envoie un message Telegram via un `Bot` éphémère.

    Le token est lu à chaque appel depuis `os.environ["TELEGRAM_BOT_TOKEN"]`
    pour éviter de le passer en argument (qui serait picklé par APScheduler).
    """
    from telegram import Bot

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise TelegramSenderError(
            "TELEGRAM_BOT_TOKEN absent de l'environnement au moment de l'envoi"
        )
    bot = Bot(token=token)
    async with bot:
        await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
    log.info("telegram_message_sent", chat_id=chat_id, chars=len(text))
