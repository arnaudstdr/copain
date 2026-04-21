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
