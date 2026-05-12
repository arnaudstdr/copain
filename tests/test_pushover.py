"""Tests du PushoverClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from bot.notifications.pushover import PushoverClient


async def test_push_nominal() -> None:
    """POST bien formé envoyé à l'API Pushover."""
    client = PushoverClient(token="tok", user="usr")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)

    with patch("bot.notifications.pushover.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        await client.push("Bonjour", title="Test", priority=0, sound="magic")

    mock_http.post.assert_awaited_once()
    call_kwargs = mock_http.post.call_args.kwargs
    assert call_kwargs["data"]["message"] == "Bonjour"
    assert call_kwargs["data"]["title"] == "Test"
    assert call_kwargs["data"]["token"] == "tok"
    assert call_kwargs["data"]["user"] == "usr"
    assert call_kwargs["data"]["sound"] == "magic"


async def test_push_nominal_no_sound() -> None:
    """Le champ `sound` est omis du payload si non fourni."""
    client = PushoverClient(token="tok", user="usr")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)

    with patch("bot.notifications.pushover.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        await client.push("Test")

    payload = mock_http.post.call_args.kwargs["data"]
    assert "sound" not in payload


async def test_push_empty_token_no_network_call() -> None:
    """Aucun appel réseau si le token est vide."""
    client = PushoverClient(token="", user="usr")
    with patch("bot.notifications.pushover.httpx.AsyncClient") as mock_cls:
        await client.push("Test")
    mock_cls.assert_not_called()


async def test_push_empty_user_no_network_call() -> None:
    """Aucun appel réseau si le user est vide."""
    client = PushoverClient(token="tok", user="")
    with patch("bot.notifications.pushover.httpx.AsyncClient") as mock_cls:
        await client.push("Test")
    mock_cls.assert_not_called()


async def test_push_network_error_is_silent() -> None:
    """Une erreur réseau ne propage pas d'exception."""
    client = PushoverClient(token="tok", user="usr")
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=httpx.ConnectError("unreachable"))

    with patch("bot.notifications.pushover.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        await client.push("Test")  # ne doit pas lever


async def test_push_http_4xx_is_silent() -> None:
    """Une réponse HTTP 4xx ne propage pas d'exception."""
    client = PushoverClient(token="tok", user="usr")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "400 Bad Request", request=MagicMock(), response=MagicMock()
        )
    )
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)

    with patch("bot.notifications.pushover.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        await client.push("Test")  # ne doit pas lever


async def test_push_timeout_is_silent() -> None:
    """Un timeout ne propage pas d'exception."""
    client = PushoverClient(token="tok", user="usr")
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    with patch("bot.notifications.pushover.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        await client.push("Test")  # ne doit pas lever
