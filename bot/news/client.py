"""Curation quotidienne des actualités IA via SearXNG + LLM.

La card actu de la PWA et tout appel manuel à `intent=news` passent par
`NewsCurator.fetch_top_news(topics)` qui :

1. Lance N recherches SearXNG en parallèle (une par topic) avec
   `time_range=day` + `categories=news` + `language=all` pour capter
   les actus FR + EN des dernières 24h.
2. Agrège les résultats, dédoublonne par URL, filtre une éventuelle
   blocklist de domaines.
3. Demande au LLM de sélectionner les 5 articles les plus IMPACTANTS
   pour le monde IA/LLM et de rédiger un résumé factuel de 1-2 phrases
   par article.

Le LLM joue ici un double rôle : filtre de pertinence (parmi 30-50
résultats bruts) et rédacteur (résumé concis). C'est ce qui permet de
passer d'un flot brut de news à un fil court et exploitable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from bot.llm.client import LLMClient
    from bot.search.searxng import SearchResult, SearxngClient

log = get_logger(__name__)

# Combien de résultats bruts on demande à SearXNG par topic. On
# sur-récupère pour donner au LLM le choix au moment de la curation.
RESULTS_PER_TOPIC = 10

# Cible finale après curation LLM.
TOP_K_DEFAULT = 5


class NewsCurator:
    """Combine SearXNG (news 24h) + LLM (curation/résumé) pour le briefing."""

    def __init__(self, searxng: SearxngClient, llm: LLMClient) -> None:
        self._searxng = searxng
        self._llm = llm

    async def fetch_top_news(
        self,
        topics: Sequence[str],
        domains_blocklist: Sequence[str] = (),
        top_k: int = TOP_K_DEFAULT,
    ) -> str:
        """Retourne un bloc texte prêt à insérer dans le briefing.

        Si `topics` est vide ou si toutes les recherches échouent, retourne
        une chaîne vide (le briefing skip la section proprement). Le LLM
        est appelé une seule fois pour curer + résumer (1 appel cloud).
        """
        if not topics:
            log.info("news_topics_empty")
            return ""

        raw_results = await self._aggregate_searches(topics)
        if not raw_results:
            log.warning("news_no_results", topics_count=len(topics))
            return ""

        deduped = _dedupe_and_filter(raw_results, domains_blocklist)
        log.info(
            "news_results_aggregated",
            raw=len(raw_results),
            deduped=len(deduped),
            topics=list(topics),
        )

        if not deduped:
            return ""

        return await self._curate_with_llm(deduped, top_k=top_k)

    async def _aggregate_searches(self, topics: Sequence[str]) -> list[SearchResult]:
        """Exécute les N recherches en parallèle, tolère les échecs partiels."""
        tasks = [
            self._searxng.search(
                query=topic,
                limit=RESULTS_PER_TOPIC,
                time_range="day",
                categories="news",
                language="all",
                bypass_cache=True,
            )
            for topic in topics
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[SearchResult] = []
        for topic, outcome in zip(topics, gathered, strict=True):
            if isinstance(outcome, BaseException):
                log.warning("news_topic_search_failed", topic=topic, error=str(outcome))
                continue
            results.extend(outcome)
        return results

    async def _curate_with_llm(
        self,
        results: Sequence[SearchResult],
        *,
        top_k: int,
    ) -> str:
        """Demande au LLM de sélectionner les top_k articles les plus impactants."""
        bullets = "\n".join(f"- {r['title']} ({r['url']})\n  {r['snippet'][:300]}" for r in results)

        system = (
            "Tu es un curateur d'actualités IA pour Arnaud, développeur Python "
            "qui travaille quotidiennement avec des LLM et des agents.\n\n"
            f"Sélectionne EXACTEMENT les {top_k} articles les plus IMPACTANTS "
            "pour le monde de l'IA/LLM parmi la liste ci-dessous.\n\n"
            "Critères de sélection (priorité dans cet ordre) :\n"
            "1. Annonces majeures de modèles (GPT-X, Claude X, Mistral, Llama, "
            "Gemini, etc.)\n"
            "2. Lancements de produits / SDK / frameworks notables\n"
            "3. Acquisitions, levées de fonds majeures (>100M$)\n"
            "4. Régulations / décisions politiques marquantes\n"
            "5. Recherches scientifiques publiées avec impact concret\n\n"
            "Évite : annonces marketing creuses, articles d'opinion, contenus "
            "thématiques généraux non liés à une actu précise du jour.\n\n"
            "Format de réponse en markdown (pas de bloc <meta>) :\n"
            "- **Titre** (source) — résumé factuel en 1-2 phrases\n"
            "  URL\n\n"
            "Si moins de "
            f"{top_k} articles sont vraiment impactants, indique-en moins. Ne "
            "comble pas avec du remplissage."
        )
        user = f"Articles candidats ({len(results)}) :\n\n{bullets}"

        return await self._llm.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            cacheable=True,
        )


def _dedupe_and_filter(
    results: Sequence[SearchResult],
    domains_blocklist: Sequence[str],
) -> list[SearchResult]:
    """Dédoublonne par URL, filtre les domaines dans la blocklist."""
    blocklist = {d.lower().removeprefix("www.") for d in domains_blocklist}
    seen_urls: set[str] = set()
    out: list[SearchResult] = []
    for r in results:
        url = r["url"]
        if not url or url in seen_urls:
            continue
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if any(host.endswith(blocked) for blocked in blocklist):
            continue
        seen_urls.add(url)
        out.append(r)
    return out


def extract_news_config(
    profile_data: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Lit `news_topics.daily_briefing` et `news_topics.filters.domains_blocklist`.

    Source attendue dans `data/profile.yaml` :

        news_topics:
          daily_briefing:
            - "LLM agents"
            - "OpenAI OR Anthropic"
          filters:
            domains_blocklist: [reddit.com, twitter.com]

    Retourne `([], [])` si la section est absente ou mal formée — on
    préfère renvoyer un fil vide plutôt qu'un crash en production.
    """
    section = profile_data.get("news_topics") or {}
    if not isinstance(section, dict):
        return [], []
    raw_topics = section.get("daily_briefing") or []
    if not isinstance(raw_topics, list):
        return [], []
    topics = [str(t).strip() for t in raw_topics if str(t).strip()]
    filters = section.get("filters") or {}
    if not isinstance(filters, dict):
        return topics, []
    raw_block = filters.get("domains_blocklist") or []
    if not isinstance(raw_block, list):
        return topics, []
    blocklist = [str(d).strip() for d in raw_block if str(d).strip()]
    return topics, blocklist
