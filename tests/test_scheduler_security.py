"""Tests de sécurité : le token Telegram ne doit pas fuiter dans scheduler.db.

Ces tests garantissent que `_send_reminder` ne prend plus le token en argument
(donc APScheduler ne le picklera pas) et qu'il le lit via `os.environ` au
moment de l'envoi.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from bot.tasks.scheduler import _send_reminder
from bot.telegram_sender import TelegramSenderError, send_message


def test_send_reminder_signature_has_no_token() -> None:
    """Garde-fou : si quelqu'un rajoute un param token, ce test saute."""
    sig = inspect.signature(_send_reminder)
    params = list(sig.parameters.keys())
    assert params == ["chat_id", "content"], (
        f"Signature inattendue {params} — le token ne doit pas être un paramètre "
        "(il serait picklé par APScheduler dans scheduler.db)."
    )


async def test_send_message_reads_token_from_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """`send_message` lit TELEGRAM_BOT_TOKEN depuis os.environ à chaque appel."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token-xyz")
    fake_bot = AsyncMock()
    fake_bot.__aenter__ = AsyncMock(return_value=fake_bot)
    fake_bot.__aexit__ = AsyncMock(return_value=None)
    fake_bot.send_message = AsyncMock()

    with patch("telegram.Bot", return_value=fake_bot) as bot_cls:
        await send_message(chat_id=42, text="hello")

    bot_cls.assert_called_once_with(token="env-token-xyz")
    fake_bot.send_message.assert_awaited_once()


async def test_send_message_raises_if_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(TelegramSenderError, match="TELEGRAM_BOT_TOKEN"):
        await send_message(chat_id=42, text="hello")


async def test_send_message_uses_markdownv2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le texte est converti en MarkdownV2 et parse_mode=MarkdownV2 est passé."""
    from telegram.constants import ParseMode

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    fake_bot = AsyncMock()
    fake_bot.__aenter__ = AsyncMock(return_value=fake_bot)
    fake_bot.__aexit__ = AsyncMock(return_value=None)
    fake_bot.send_message = AsyncMock()

    with patch("telegram.Bot", return_value=fake_bot):
        await send_message(chat_id=42, text="Voici **le gras** et un point.")

    kwargs = fake_bot.send_message.call_args.kwargs
    assert kwargs["parse_mode"] == ParseMode.MARKDOWN_V2
    # MarkdownV2 : ** → *, et les . doivent être échappés (\.)
    assert "*le gras*" in kwargs["text"]
    assert "\\." in kwargs["text"]


async def test_send_message_falls_back_to_plain_on_bad_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si Telegram rejette le MarkdownV2, on retente en texte brut."""
    from telegram.error import BadRequest

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    fake_bot = AsyncMock()
    fake_bot.__aenter__ = AsyncMock(return_value=fake_bot)
    fake_bot.__aexit__ = AsyncMock(return_value=None)
    # 1er appel : BadRequest ; 2e appel : succès (fallback)
    fake_bot.send_message = AsyncMock(side_effect=[BadRequest("bad entities"), None])

    with patch("telegram.Bot", return_value=fake_bot):
        await send_message(chat_id=42, text="Un **truc** tordu.")

    assert fake_bot.send_message.await_count == 2
    # Le 2e appel (fallback) n'a pas de parse_mode
    fallback_kwargs = fake_bot.send_message.call_args_list[1].kwargs
    assert "parse_mode" not in fallback_kwargs
    assert fallback_kwargs["text"] == "Un **truc** tordu."
