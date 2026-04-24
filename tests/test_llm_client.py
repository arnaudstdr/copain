"""Tests du LLMClient, notamment l'encodage base64 des images."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock

import pytest

from bot.llm.client import LLMClient, LLMError


@pytest.fixture
def fake_client() -> LLMClient:
    client = LLMClient(
        base_url="http://localhost:11434",
        model="gemma4:31b-cloud",
        cache_ttl_sec=None,  # désactivé pour ne pas biaiser les asserts call_args
    )
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


async def test_chat_passes_num_ctx_option(fake_client: LLMClient) -> None:
    """Le num_ctx doit être passé à chaque appel pour court-circuiter le default Ollama."""
    await fake_client.chat([{"role": "user", "content": "salut"}])
    args = fake_client._client.chat.call_args  # type: ignore[attr-defined]
    assert args.kwargs["options"] == {"num_ctx": LLMClient.DEFAULT_NUM_CTX}


async def test_chat_uses_configured_num_ctx() -> None:
    """Le num_ctx passé au constructeur est bien celui envoyé à Ollama."""
    client = LLMClient(base_url="http://x", model="m", num_ctx=16384, cache_ttl_sec=None)
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(return_value={"message": {"content": "ok"}})
    await client.chat([{"role": "user", "content": "salut"}])
    args = client._client.chat.call_args  # type: ignore[attr-defined]
    assert args.kwargs["options"] == {"num_ctx": 16384}


async def test_chat_cacheable_hit_does_not_call_ollama() -> None:
    """Deux appels cacheable avec les mêmes messages : un seul hit Ollama."""
    client = LLMClient(base_url="http://x", model="m", cache_ttl_sec=60.0)
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(return_value={"message": {"content": "réponse"}})
    msgs = [{"role": "user", "content": "salut"}]
    r1 = await client.chat(msgs, cacheable=True)
    r2 = await client.chat(msgs, cacheable=True)
    assert r1 == r2 == "réponse"
    assert client._client.chat.call_count == 1  # type: ignore[attr-defined]


async def test_chat_non_cacheable_always_calls_ollama() -> None:
    """Le cache est opt-in : sans cacheable=True on rappelle Ollama à chaque fois."""
    client = LLMClient(base_url="http://x", model="m", cache_ttl_sec=60.0)
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(return_value={"message": {"content": "réponse"}})
    msgs = [{"role": "user", "content": "salut"}]
    await client.chat(msgs)
    await client.chat(msgs)
    assert client._client.chat.call_count == 2  # type: ignore[attr-defined]


async def test_chat_cache_disabled_via_none_ttl() -> None:
    """cache_ttl_sec=None doit désactiver totalement le cache, même avec cacheable=True."""
    client = LLMClient(base_url="http://x", model="m", cache_ttl_sec=None)
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(return_value={"message": {"content": "réponse"}})
    msgs = [{"role": "user", "content": "salut"}]
    await client.chat(msgs, cacheable=True)
    await client.chat(msgs, cacheable=True)
    assert client._client.chat.call_count == 2  # type: ignore[attr-defined]


async def test_call_with_search_is_cached() -> None:
    """`call_with_search` passe cacheable=True → deux appels identiques = un seul hit."""
    client = LLMClient(base_url="http://x", model="m", cache_ttl_sec=60.0)
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(return_value={"message": {"content": "résumé"}})
    results = [{"title": "t", "url": "http://u", "snippet": "s"}]
    await client.call_with_search("question", results)
    await client.call_with_search("question", results)
    assert client._client.chat.call_count == 1  # type: ignore[attr-defined]


async def test_fallback_triggered_on_primary_timeout() -> None:
    """Quand primary timeout, fallback est appelé et sa réponse renvoyée."""
    import httpx

    client = LLMClient(
        base_url="http://primary",
        model="gemma4:31b-cloud",
        cache_ttl_sec=None,
        fallback_model="gemma3:4b",
        fallback_base_url="http://localhost:11434",
    )
    # Primary : timeout
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(side_effect=httpx.ReadTimeout("too slow"))
    # Fallback : succès
    assert client._fallback is not None
    client._fallback.client = AsyncMock()  # type: ignore[assignment]
    client._fallback.client.chat = AsyncMock(
        return_value={"message": {"content": "réponse fallback"}}
    )

    result = await client.chat([{"role": "user", "content": "salut"}])
    assert result == "réponse fallback"
    assert client._client.chat.call_count == 1  # type: ignore[attr-defined]
    assert client._fallback.client.chat.call_count == 1


async def test_fallback_triggered_on_primary_generic_error() -> None:
    """Un LLMError générique (pas timeout) déclenche aussi le fallback."""
    client = LLMClient(
        base_url="http://primary",
        model="m1",
        cache_ttl_sec=None,
        fallback_model="m2",
    )
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(side_effect=ConnectionError("network down"))
    assert client._fallback is not None
    client._fallback.client = AsyncMock()  # type: ignore[assignment]
    client._fallback.client.chat = AsyncMock(return_value={"message": {"content": "fallback ok"}})
    result = await client.chat([{"role": "user", "content": "salut"}])
    assert result == "fallback ok"


async def test_fallback_skipped_when_images_present() -> None:
    """Si la requête contient des images, on n'essaie pas le fallback (modèle non multimodal)."""
    import httpx

    from bot.llm.client import LLMTimeoutError

    client = LLMClient(
        base_url="http://primary",
        model="m1",
        cache_ttl_sec=None,
        fallback_model="m2",
    )
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
    assert client._fallback is not None
    client._fallback.client = AsyncMock()  # type: ignore[assignment]
    client._fallback.client.chat = AsyncMock()

    msgs = [{"role": "user", "content": "décris", "images": ["base64..."]}]
    with pytest.raises(LLMTimeoutError):
        await client.chat(msgs)
    assert client._fallback.client.chat.call_count == 0


async def test_fallback_failure_reraises_primary_error() -> None:
    """Si le fallback plante aussi, on ré-émet l'erreur primary (UX cohérente)."""
    import httpx

    from bot.llm.client import LLMTimeoutError

    client = LLMClient(
        base_url="http://primary",
        model="m1",
        cache_ttl_sec=None,
        fallback_model="m2",
    )
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
    assert client._fallback is not None
    client._fallback.client = AsyncMock()  # type: ignore[assignment]
    client._fallback.client.chat = AsyncMock(side_effect=ConnectionError("no local"))

    with pytest.raises(LLMTimeoutError):
        await client.chat([{"role": "user", "content": "salut"}])


async def test_no_fallback_configured_reraises() -> None:
    """Sans fallback_model, on ré-émet l'erreur primary directement."""
    import httpx

    from bot.llm.client import LLMTimeoutError

    client = LLMClient(base_url="http://x", model="m", cache_ttl_sec=None)
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
    assert client._fallback is None
    with pytest.raises(LLMTimeoutError):
        await client.chat([{"role": "user", "content": "salut"}])


async def test_fallback_result_not_cached() -> None:
    """Les réponses fallback ne doivent pas être mises en cache (moins fiables)."""
    import httpx

    client = LLMClient(
        base_url="http://primary",
        model="m1",
        cache_ttl_sec=60.0,
        fallback_model="m2",
    )
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
    assert client._fallback is not None
    client._fallback.client = AsyncMock()  # type: ignore[assignment]
    client._fallback.client.chat = AsyncMock(return_value={"message": {"content": "fallback"}})

    msgs = [{"role": "user", "content": "salut"}]
    await client.chat(msgs, cacheable=True)

    # Deuxième appel : primary toujours timeout. Si fallback était caché,
    # on n'aurait pas rappelé fallback. Mais comme il n'est pas caché,
    # fallback devrait être réappelé.
    await client.chat(msgs, cacheable=True)
    assert client._fallback.client.chat.call_count == 2
