"""Tests du streaming LLM + TelegramStreamSink + visible_text."""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers import BotDeps, _process
from bot.llm.client import LLMClient
from bot.telegram_sender import TelegramStreamSink, visible_text

# -- visible_text ---------------------------------------------------------


def test_visible_text_strips_full_meta_block() -> None:
    buf = 'Bonjour.\n<meta>{"intent":"answer"}</meta>'
    assert visible_text(buf) == "Bonjour."


def test_visible_text_strips_unclosed_meta() -> None:
    """Stream encore en cours : bloc <meta> ouvert, pas fermé."""
    buf = 'Bonjour.\n<meta>{"intent":"ans'
    assert visible_text(buf) == "Bonjour."


def test_visible_text_strips_partial_open_tag() -> None:
    """Fin de buffer = début de <meta> en cours d'assemblage."""
    assert visible_text("Bonjour.\n<me") == "Bonjour."
    assert visible_text("Bonjour.\n<meta") == "Bonjour."


def test_visible_text_no_meta_passes_through() -> None:
    assert visible_text("Bonjour, ça va ?") == "Bonjour, ça va ?"


# -- LLMClient.chat_stream -----------------------------------------------


class _FakeStream:
    def __init__(self, chunks: list[dict]) -> None:  # type: ignore[type-arg]
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[dict]:  # type: ignore[type-arg]
        async def gen() -> AsyncIterator[dict]:  # type: ignore[type-arg]
            for chunk in self._chunks:
                yield chunk

        return gen()


async def test_chat_stream_yields_chunks() -> None:
    client = LLMClient(base_url="http://x", model="m", cache_ttl_sec=None)
    client._client = AsyncMock()  # type: ignore[assignment]

    chunks = [
        {"message": {"content": "Bon"}},
        {"message": {"content": "jour "}},
        {"message": {"content": "Arnaud"}},
    ]
    client._client.chat = AsyncMock(return_value=_FakeStream(chunks))

    pieces = [p async for p in client.chat_stream([{"role": "user", "content": "salut"}])]
    assert pieces == ["Bon", "jour ", "Arnaud"]


async def test_chat_stream_cache_hit_yields_once() -> None:
    client = LLMClient(base_url="http://x", model="m", cache_ttl_sec=60.0)
    client._client = AsyncMock()  # type: ignore[assignment]
    client._client.chat = AsyncMock(return_value=_FakeStream([{"message": {"content": "complet"}}]))

    # Premier appel (cacheable) : chaîne le stream + cache le résultat.
    async for _ in client.chat_stream([{"role": "user", "content": "x"}], cacheable=True):
        pass
    # Deuxième appel : doit hitter le cache (un seul yield avec le texte complet).
    client._client.chat = AsyncMock(side_effect=AssertionError("ne doit pas être rappelé"))
    pieces = [
        p async for p in client.chat_stream([{"role": "user", "content": "x"}], cacheable=True)
    ]
    assert pieces == ["complet"]


async def test_chat_stream_fallback_on_timeout_before_first_chunk() -> None:
    """Si primary timeout AVANT le premier chunk, fallback (non-streamé)."""
    import httpx

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
    client._fallback.client.chat = AsyncMock(
        return_value={"message": {"content": "réponse fallback"}}
    )

    pieces = [p async for p in client.chat_stream([{"role": "user", "content": "x"}])]
    assert pieces == ["réponse fallback"]


# -- TelegramStreamSink --------------------------------------------------


def _fake_message() -> MagicMock:
    """Message PTB mocké ; reply_text retourne un objet avec edit_text asynchrone."""
    sent = MagicMock()
    sent.edit_text = AsyncMock()
    message = MagicMock()
    message.reply_text = AsyncMock(return_value=sent)
    return message


async def test_sink_first_emit_sends_reply() -> None:
    msg = _fake_message()
    sink = TelegramStreamSink(msg, min_edit_interval_sec=0.0)
    await sink.emit("Bonjour")
    msg.reply_text.assert_awaited_once()
    assert msg.reply_text.await_args.args[0] == "Bonjour"


async def test_sink_subsequent_emits_edit_sent_message() -> None:
    msg = _fake_message()
    sink = TelegramStreamSink(msg, min_edit_interval_sec=0.0)
    await sink.emit("Bon")
    sent = msg.reply_text.return_value
    await sink.emit("Bonjour")
    sent.edit_text.assert_awaited_once()
    assert sent.edit_text.await_args.args[0] == "Bonjour"


async def test_sink_debounces_rapid_emits() -> None:
    """Avec un intervalle non-nul, deux emits rapides ne doivent pas tous deux editer."""
    msg = _fake_message()
    sink = TelegramStreamSink(msg, min_edit_interval_sec=1.0)
    await sink.emit("a")
    sent = msg.reply_text.return_value
    await sink.emit("ab")  # trop tôt → skipped
    sent.edit_text.assert_not_called()


async def test_sink_skips_identical_emit() -> None:
    msg = _fake_message()
    sink = TelegramStreamSink(msg, min_edit_interval_sec=0.0)
    await sink.emit("même")
    await sink.emit("même")
    sent = msg.reply_text.return_value
    sent.edit_text.assert_not_called()


async def test_sink_finalize_uses_markdown() -> None:
    msg = _fake_message()
    sink = TelegramStreamSink(msg, min_edit_interval_sec=0.0)
    await sink.emit("intro")
    sent = msg.reply_text.return_value
    await sink.finalize("**Bonjour** Arnaud")
    # MarkdownV2 avec parse_mode
    assert sent.edit_text.await_args.kwargs.get("parse_mode") is not None


async def test_sink_finalize_without_prior_emit_falls_back_to_reply_markdown() -> None:
    msg = _fake_message()
    sink = TelegramStreamSink(msg, min_edit_interval_sec=0.0)
    await sink.finalize("message direct")
    # reply_text a été appelé (pas edit_text)
    msg.reply_text.assert_awaited_once()


# -- Intégration _process avec sink --------------------------------------


@pytest.fixture
def streaming_deps() -> BotDeps:
    """BotDeps mocké dont llm.chat_stream streame des chunks prédéfinis."""
    settings = MagicMock()
    settings.allowed_user_id = 42
    settings.timezone = "Europe/Paris"
    settings.home_city = "Sélestat"
    settings.home_lat = 48.26
    settings.home_lon = 7.45
    settings.fuel_default_radius_km = 10.0

    memory = MagicMock()
    memory.retrieve_context = AsyncMock(return_value=[])
    memory.store = AsyncMock()

    llm = MagicMock()
    raw = (
        "Bonjour Arnaud.\n"
        '<meta>{"intent":"answer","store_memory":false,"memory_content":null,'
        '"task":{"content":null,"due_str":null},'
        '"feed":{"action":null,"name":null,"url":null},'
        '"event":{"action":null,"title":null,"start_str":null,"end_str":null,'
        '"location":null,"description":null,"range_str":null,"calendar_name":null},'
        '"fuel":{"fuel_type":null,"radius_km":null,"location":null},'
        '"weather":{"location":null,"when":null},'
        '"search_query":null}</meta>'
    )
    pieces = [raw[i : i + 10] for i in range(0, len(raw), 10)]

    async def stream(*_args: object, **_kwargs: object) -> AsyncIterator[str]:
        for p in pieces:
            yield p

    llm.chat_stream = stream
    llm.call = AsyncMock()  # ne doit pas être appelé en mode streaming sans images

    return BotDeps(
        settings=settings,
        llm=llm,
        memory=memory,
        tasks=MagicMock(),
        scheduler=MagicMock(),
        search=MagicMock(),
        rss=MagicMock(),
        rss_fetcher=MagicMock(),
        briefing=MagicMock(),
        calendar=MagicMock(),
        fuel=MagicMock(),
        geocoder=MagicMock(),
        weather=MagicMock(),
        history=deque(maxlen=6),
    )


async def test_process_with_sink_streams_via_chat_stream(streaming_deps: BotDeps) -> None:
    msg = _fake_message()
    sink = TelegramStreamSink(msg, min_edit_interval_sec=0.0)
    text = await _process("salut", chat_id=42, deps=streaming_deps, sink=sink)
    assert text == "Bonjour Arnaud."
    # llm.call n'est pas utilisé en streaming
    streaming_deps.llm.call.assert_not_called()
    # au moins un emit (premier reply_text)
    msg.reply_text.assert_awaited()


async def test_process_with_sink_but_images_uses_non_streaming(
    streaming_deps: BotDeps,
) -> None:
    """Avec images, on ne stream pas (modèle local fallback pas multimodal)."""
    streaming_deps.llm.call = AsyncMock(
        return_value='ok\n<meta>{"intent":"answer","store_memory":false,"memory_content":null,'
        '"task":{"content":null,"due_str":null},'
        '"feed":{"action":null,"name":null,"url":null},'
        '"event":{"action":null,"title":null,"start_str":null,"end_str":null,'
        '"location":null,"description":null,"range_str":null,"calendar_name":null},'
        '"fuel":{"fuel_type":null,"radius_km":null,"location":null},'
        '"weather":{"location":null,"when":null},"search_query":null}</meta>'
    )
    msg = _fake_message()
    sink = TelegramStreamSink(msg, min_edit_interval_sec=0.0)
    await _process(
        "décris",
        chat_id=42,
        deps=streaming_deps,
        images=[b"fake"],
        sink=sink,
    )
    streaming_deps.llm.call.assert_awaited_once()
