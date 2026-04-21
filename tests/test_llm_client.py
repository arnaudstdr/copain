"""Tests du LLMClient, notamment l'encodage base64 des images."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock

import pytest

from bot.llm.client import LLMClient, LLMError


@pytest.fixture
def fake_client() -> LLMClient:
    client = LLMClient(base_url="http://localhost:11434", model="gemma4:31b-cloud")
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(return_value={"message": {"content": "analyse OK"}})
    return client


async def test_call_text_only_passes_messages_as_is(fake_client: LLMClient) -> None:
    await fake_client.call(system="sys", user="hello")
    args = fake_client._client.chat.call_args  # type: ignore[attr-defined]
    messages = args.kwargs["messages"]
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[1] == {"role": "user", "content": "hello"}
    assert "images" not in messages[1]


async def test_call_with_image_base64_encodes(fake_client: LLMClient) -> None:
    raw = b"\x89PNG\r\n\x1a\nfakepayload"
    await fake_client.call(system="sys", user="décris", images=[raw])
    args = fake_client._client.chat.call_args  # type: ignore[attr-defined]
    user_msg = args.kwargs["messages"][1]
    assert "images" in user_msg
    assert user_msg["images"] == [base64.b64encode(raw).decode("ascii")]


async def test_call_with_multiple_images(fake_client: LLMClient) -> None:
    imgs = [b"img1", b"img2"]
    await fake_client.call(system="sys", user="", images=imgs)
    args = fake_client._client.chat.call_args  # type: ignore[attr-defined]
    user_msg = args.kwargs["messages"][1]
    assert len(user_msg["images"]) == 2


async def test_chat_empty_content_raises(fake_client: LLMClient) -> None:
    fake_client._client.chat = AsyncMock(return_value={"message": {"content": ""}})  # type: ignore[attr-defined]
    with pytest.raises(LLMError, match="sans contenu"):
        await fake_client.chat([{"role": "user", "content": "salut"}])


async def test_chat_ollama_exception_wraps_as_llmerror(fake_client: LLMClient) -> None:
    fake_client._client.chat = AsyncMock(side_effect=ConnectionError("boom"))  # type: ignore[attr-defined]
    with pytest.raises(LLMError, match="Ollama échoué"):
        await fake_client.chat([{"role": "user", "content": "salut"}])


async def test_chat_timeout_raises_llmtimeout_error(fake_client: LLMClient) -> None:
    """Un httpx.TimeoutException doit lever LLMTimeoutError, pas LLMError générique."""
    import httpx

    from bot.llm.client import LLMTimeoutError

    fake_client._client.chat = AsyncMock(  # type: ignore[attr-defined]
        side_effect=httpx.ReadTimeout("too slow")
    )
    with pytest.raises(LLMTimeoutError, match="n'a pas répondu"):
        await fake_client.chat([{"role": "user", "content": "salut"}])


async def test_llmtimeout_is_subclass_of_llmerror() -> None:
    """Garantit qu'un except LLMError existant continue de capter les timeouts."""
    from bot.llm.client import LLMTimeoutError

    assert issubclass(LLMTimeoutError, LLMError)
