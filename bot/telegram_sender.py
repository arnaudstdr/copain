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

Ce module expose aussi `TelegramStreamSink` : un helper qui reçoit du texte
progressif (via `emit`) et l'affiche en éditant un message Telegram avec
un debounce, puis finalise le rendu en MarkdownV2 via `finalize`.
"""

from __future__ import annotations

import contextlib
import os
import re
import time
from typing import TYPE_CHECKING

import telegramify_markdown
from telegram.constants import ParseMode
from telegram.error import BadRequest

from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from telegram import Message

log = get_logger(__name__)

_META_BLOCK_RE = re.compile(r"<meta>.*?</meta>", re.DOTALL | re.IGNORECASE)
# Balise `<meta>` en cours de construction (ouvrante partielle à la fin).
_META_PARTIAL_OPEN_RE = re.compile(r"<m(?:e(?:t(?:a>?)?)?)?$", re.IGNORECASE)
# Balise `<meta>` complète suivie de contenu sans fermeture : on coupe à `<meta>`.
_META_OPEN_UNCLOSED_RE = re.compile(r"<meta>.*$", re.DOTALL | re.IGNORECASE)


def visible_text(buffer: str) -> str:
    """Retire le bloc `<meta>` (complet ou en cours) pour l'affichage progressif.

    Le LLM émet `<meta>{...}</meta>` à la FIN de la réponse. Pendant le
    streaming on ne veut pas l'afficher : on strip les blocs complets, les
    blocs ouverts mais pas encore fermés, et les fragments d'ouverture
    `<me…` collés en fin de buffer.
    """
    cleaned = _META_BLOCK_RE.sub("", buffer)
    cleaned = _META_OPEN_UNCLOSED_RE.sub("", cleaned)
    cleaned = _META_PARTIAL_OPEN_RE.sub("", cleaned)
    return cleaned.rstrip()


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


class TelegramStreamSink:
    """Affichage progressif d'une réponse LLM dans un message Telegram.

    - Le premier `emit(text)` non vide envoie un message via `reply_text`.
    - Les `emit` suivants éditent ce même message avec un debounce
      (`min_edit_interval_sec`) pour éviter de spammer l'API Telegram
      (limite rate ~1 edit/s sur un même message).
    - `finalize(final_text)` force un rendu MarkdownV2 final propre.
    - Les emits successifs qui donneraient le même texte sont ignorés
      (Telegram rejette un edit identique avec BadRequest).
    """

    DEFAULT_MIN_EDIT_INTERVAL_SEC = 0.8

    def __init__(
        self,
        message: Message,
        min_edit_interval_sec: float = DEFAULT_MIN_EDIT_INTERVAL_SEC,
    ) -> None:
        self._message = message
        self._sent: Message | None = None
        self._min_interval = min_edit_interval_sec
        self._last_edit_monotonic = 0.0
        self._last_displayed = ""

    @property
    def has_sent(self) -> bool:
        return self._sent is not None

    async def emit(self, text: str) -> None:
        """Pousse une version plus récente du texte à afficher."""
        text = text.strip()
        if not text or text == self._last_displayed:
            return

        if self._sent is None:
            self._sent = await self._message.reply_text(text, disable_web_page_preview=True)
            self._last_displayed = text
            self._last_edit_monotonic = time.monotonic()
            return

        now = time.monotonic()
        if now - self._last_edit_monotonic < self._min_interval:
            return
        try:
            await self._sent.edit_text(text, disable_web_page_preview=True)
        except BadRequest as exc:
            # « Message is not modified » : Telegram rejette un edit identique.
            log.debug("stream_edit_skipped", error=str(exc))
            return
        self._last_displayed = text
        self._last_edit_monotonic = now

    async def finalize(self, final_text: str) -> None:
        """Envoie/édite le message final rendu en MarkdownV2 (fallback plain-text)."""
        if self._sent is None:
            await reply_markdown(self._message, final_text)
            return
        rendered = markdownify(final_text)
        try:
            await self._sent.edit_text(
                rendered,
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            log.warning("stream_markdown_final_failed", error=str(exc))
            with contextlib.suppress(BadRequest):
                await self._sent.edit_text(final_text, disable_web_page_preview=True)
        self._last_displayed = final_text
