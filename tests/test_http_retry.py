"""Tests du helper bot.http_retry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from bot import http_retry
from bot.http_retry import get_json_with_retry


class _DemoError(RuntimeError):
    pass


def _success_response(payload: dict[str, object]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


async def test_get_json_with_retry_returns_payload_on_success() -> None:
    client = AsyncMock()
    client.get = AsyncMock(return_value=_success_response({"ok": True}))

    result = await get_json_with_retry(
        client,
        url="https://example.com/api",
        context="demo",
        error_cls=_DemoError,
    )
    assert result == {"ok": True}
    assert client.get.await_count == 1


async def test_get_json_with_retry_retries_on_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http_retry, "DEFAULT_BACKOFF_SECONDS", (0.0, 0.0))
    success = _success_response({"ok": True})

    client = AsyncMock()
    client.get = AsyncMock(
        side_effect=[
            httpx.ReadTimeout("timeout 1"),
            httpx.ReadTimeout("timeout 2"),
            success,
        ]
    )

    result = await get_json_with_retry(
        client,
        url="https://example.com/api",
        context="demo",
        error_cls=_DemoError,
        backoff_seconds=(0.0, 0.0),
    )
    assert result == {"ok": True}
    assert client.get.await_count == 3


async def test_get_json_with_retry_raises_custom_error_after_exhaustion() -> None:
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(_DemoError) as excinfo:
        await get_json_with_retry(
            client,
            url="https://example.com/api",
            context="demo",
            error_cls=_DemoError,
            backoff_seconds=(0.0, 0.0),
        )
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)
    assert client.get.await_count == 3


async def test_get_json_with_retry_does_not_retry_on_malformed_payload() -> None:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.side_effect = ValueError("not json")

    client = AsyncMock()
    client.get = AsyncMock(return_value=response)

    with pytest.raises(_DemoError):
        await get_json_with_retry(
            client,
            url="https://example.com/api",
            context="demo",
            error_cls=_DemoError,
        )
    assert client.get.await_count == 1


async def test_get_json_with_retry_respects_custom_max_attempts() -> None:
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))

    with pytest.raises(_DemoError):
        await get_json_with_retry(
            client,
            url="https://example.com/api",
            context="demo",
            error_cls=_DemoError,
            max_attempts=2,
            backoff_seconds=(0.0,),
        )
    assert client.get.await_count == 2
