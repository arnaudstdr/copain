"""Helper unique pour envoyer un message Telegram hors du flux PTB.

Utilisé par les jobs APScheduler (rappels, briefing matinal) qui s'exécutent
en dehors du cycle `update → handler`. Lire le token via `os.environ` plutôt
que de le sérialiser dans les args du job évite qu'il se retrouve pickle
dans le jobstore SQLAlchemy (`data/scheduler.db`).

Les messages sont convertis en **MarkdownV2** Telegram via la lib
`telegramify-markdown`. Le LLM produit du Markdown standard (``**bold**``,
``[lien](url)``, listes ``- item``) et on laisse la lib gérer l'échappement
des caractères spéciaux MarkdownV2 (``. - ( ) ! { } etc.``). Un fallback
plain-text s'active si Telegram rejette le rendu (rare mais toujours
possible sur du Markdown très exotique).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import telegramify_markdown
from telegram.constants import ParseMode
from telegram.error import BadRequest

from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from telegram import Message

log = get_logger(__name__)


class TelegramSenderError(RuntimeError):
    """Levée si le token est absent de l'environnement au moment de l'envoi."""


def markdownify(text: str) -> str:
    """Convertit du Markdown standard en MarkdownV2 Telegram (échappements gérés)."""
    rendered: str = telegramify_markdown.markdownify(text)
    return rendered


async def send_message(chat_id: int, text: str) -> None:
    """Envoie un message Telegram via un `Bot` éphémère, en MarkdownV2.

    Le token est lu à chaque appel depuis `os.environ["TELEGRAM_BOT_TOKEN"]`
    pour éviter de le passer en argument (qui serait picklé par APScheduler).

    Fallback plain-text si Telegram rejette le rendu MarkdownV2 (ex: entité
    mal formée que la lib aurait laissé passer). Le message n'est jamais
    perdu, au pire juste moins joli.
    """
    from telegram import Bot

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise TelegramSenderError(
            "TELEGRAM_BOT_TOKEN absent de l'environnement au moment de l'envoi"
        )
    bot = Bot(token=token)
    async with bot:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=markdownify(text),
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            log.warning("markdown_render_failed_fallback_plain", error=str(exc))
            await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
    log.info("telegram_message_sent", chat_id=chat_id, chars=len(text))


async def reply_markdown(message: Message, text: str) -> None:
    """Réponse à un message Telegram via `reply_text`, en MarkdownV2.

    Utilisé par les handlers PTB (text + photo) pour garder le threading
    natif (`reply_to`) tout en bénéficiant du rendu Markdown.
    """
    try:
        await message.reply_text(
            markdownify(text),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        log.warning("markdown_reply_failed_fallback_plain", error=str(exc))
        await message.reply_text(text, disable_web_page_preview=True)
