"""Tests du `NewsCurator` (agrégation SearXNG + curation LLM)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from bot.news.client import NewsCurator, _dedupe_and_filter
from bot.search.searxng import SearchResult


def _result(title: str, url: str, snippet: str = "") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet)


# --- helpers internes -----------------------------------------------------


def test_dedupe_removes_same_url() -> None:
    results = [
        _result("A", "https://a.com/1"),
        _result("A bis", "https://a.com/1"),  # même URL
        _result("B", "https://b.com/2"),
    ]
    out = _dedupe_and_filter(results, ())
    assert [r["url"] for r in out] == ["https://a.com/1", "https://b.com/2"]


def test_dedupe_filters_blocked_domains() -> None:
    results = [
        _result("Forum post", "https://reddit.com/r/AI/x"),
        _result("Article", "https://lemonde.fr/tech/y"),
        _result("Tweet", "https://www.twitter.com/elonmusk/status/123"),
    ]
    out = _dedupe_and_filter(results, ("reddit.com", "twitter.com"))
    assert len(out) == 1
    assert out[0]["url"] == "https://lemonde.fr/tech/y"


def test_dedupe_skips_empty_urls() -> None:
    results = [_result("ok", "https://a.com/x"), _result("orphan", "")]
    out = _dedupe_and_filter(results, ())
    assert [r["url"] for r in out] == ["https://a.com/x"]


# --- NewsCurator.fetch_top_news ------------------------------------------


def _make_curator(
    *,
    searxng_results: list[list[SearchResult]] | None = None,
    searxng_side_effect: BaseException | None = None,
    llm_response: str = "- **Titre** (source) — résumé\n  https://x.com/1",
) -> tuple[NewsCurator, MagicMock, MagicMock]:
    searxng = MagicMock()
    if searxng_side_effect is not None:
        searxng.search = AsyncMock(side_effect=searxng_side_effect)
    else:
        searxng.search = AsyncMock(side_effect=searxng_results or [[]])
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=llm_response)
    return NewsCurator(searxng=searxng, llm=llm), searxng, llm


async def test_fetch_top_news_empty_topics_returns_empty() -> None:
    curator, searxng, llm = _make_curator()
    out = await curator.fetch_top_news(topics=[])
    assert out == ""
    searxng.search.assert_not_called()
    llm.chat.assert_not_called()


async def test_fetch_top_news_no_results_returns_empty() -> None:
    """Toutes les requêtes retournent [] → on skip le LLM."""
    curator, _searxng, llm = _make_curator(searxng_results=[[], [], []])
    out = await curator.fetch_top_news(topics=["a", "b", "c"])
    assert out == ""
    llm.chat.assert_not_called()


async def test_fetch_top_news_aggregates_and_calls_llm() -> None:
    curator, searxng, llm = _make_curator(
        searxng_results=[
            [_result("News 1", "https://example.com/1", "snippet 1")],
            [_result("News 2", "https://example.com/2", "snippet 2")],
        ],
        llm_response="- **News 1** (example.com) — top.\n  https://example.com/1",
    )
    out = await curator.fetch_top_news(topics=["LLM agents", "OpenAI"])
    assert "News 1" in out
    # 2 requêtes SearXNG
    assert searxng.search.await_count == 2
    # 1 appel LLM avec les 2 résultats agrégés
    llm.chat.assert_awaited_once()


async def test_fetch_top_news_tolerates_partial_failure() -> None:
    """Si une requête sur 3 échoue, on continue avec les 2 autres."""
    searxng = MagicMock()
    searxng.search = AsyncMock(
        side_effect=[
            [_result("OK 1", "https://a.com/1")],
            RuntimeError("SearXNG timeout"),
            [_result("OK 3", "https://c.com/3")],
        ]
    )
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="- **OK** (source) — ok.")
    curator = NewsCurator(searxng=searxng, llm=llm)

    out = await curator.fetch_top_news(topics=["a", "b", "c"])
    assert "OK" in out
    # LLM appelé même si 1 requête a planté
    llm.chat.assert_awaited_once()


async def test_fetch_top_news_uses_day_news_language_all() -> None:
    """Le curator force time_range=day, categories=news, language=all."""
    curator, searxng, _llm = _make_curator(searxng_results=[[_result("X", "https://x.com/1")]])
    await curator.fetch_top_news(topics=["LLM"])
    kwargs = searxng.search.await_args.kwargs
    assert kwargs["time_range"] == "day"
    assert kwargs["categories"] == "news"
    assert kwargs["language"] == "all"
    assert kwargs["bypass_cache"] is True


async def test_fetch_top_news_applies_domains_blocklist() -> None:
    curator, _searxng, llm = _make_curator(
        searxng_results=[
            [
                _result("Forum", "https://reddit.com/r/AI/x"),
                _result("Real", "https://techcrunch.com/y"),
            ]
        ],
    )
    await curator.fetch_top_news(topics=["AI"], domains_blocklist=["reddit.com"])
    # Le LLM doit voir uniquement techcrunch dans son prompt.
    sent_user = llm.chat.await_args.kwargs["messages"][1]["content"]
    assert "techcrunch.com" in sent_user
    assert "reddit.com" not in sent_user
