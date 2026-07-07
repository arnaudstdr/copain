"""Mémoire sémantique : ChromaDB + embeddings Ollama nomic-embed-text."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import chromadb
from chromadb.api.types import IncludeEnum
from chromadb.config import Settings as ChromaSettings

from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from bot.memory.embeddings import Embedder

log = get_logger(__name__)

COLLECTION_NAME = "personal_memory"

# Config HNSW pour la collection ChromaDB.
# - `cosine` convient pour `nomic-embed-text` (vecteurs non-normalisés).
# - `M=16` : valeur recommandée pour des corpus <100k embeddings.
# - `construction_ef=128` : build un peu plus long mais meilleur rappel.
# - `search_ef=64` : meilleur rappel que le défaut (10) pour un coût faible
#   sur des top_k petits (≤5 ici), compense la taille variable du corpus.
# Cette metadata n'est appliquée QU'À la création ; sur un chroma_dir
# existant, ChromaDB conserve la metadata de départ (on log un avertissement
# si divergence pour inviter à régénérer la collection manuellement).
HNSW_METADATA: dict[str, str | int] = {
    "hnsw:space": "cosine",
    "hnsw:M": 16,
    "hnsw:construction_ef": 128,
    "hnsw:search_ef": 64,
}

# Pondération de `retrieve_context` : on sur-échantillonne (RAG_OVERSAMPLE fois
# top_k) avant de re-classer, pour que le seuil de distance et le boost de
# récence puissent départager plus large que le top_k final.
RAG_OVERSAMPLE = 4
# Bonus de récence maximal soustrait à la distance cosine d'un souvenir tout
# frais (récence = 1). Volontairement petit : il départage à pertinence
# sémantique voisine sans jamais écraser un vrai match lointain dans le temps.
RAG_RECENCY_MAX_BONUS = 0.15


class DepotMatch(NamedTuple):
    """Match de similarité sur un dépôt cognitif indexé dans ChromaDB.

    `thought_id` pointe vers la ligne SQLite `thoughts` ; la validation de
    son existence reste à la charge de l'appelant (vérité = SQLite).
    """

    thought_id: int
    content: str
    distance: float


class MemoryManager:
    """Stocke et récupère des souvenirs factuels via embeddings vectoriels.

    Le `memory_content` (résumé factuel produit par le LLM) est ce qui est
    embedded. Le message brut est conservé en metadata pour debug uniquement.
    """

    def __init__(
        self,
        persist_dir: Path,
        embedder: Embedder,
        *,
        rag_max_distance: float = 0.6,
        rag_recency_half_life_days: float = 30.0,
    ) -> None:
        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata=HNSW_METADATA,
        )
        self._embedder = embedder
        self._rag_max_distance = rag_max_distance
        self._rag_recency_half_life_days = rag_recency_half_life_days
        self._warn_on_hnsw_drift()

    def _warn_on_hnsw_drift(self) -> None:
        """Log un warning si une collection existante tourne avec une config HNSW différente."""
        actual = self._collection.metadata or {}
        drift = {k: (actual.get(k), v) for k, v in HNSW_METADATA.items() if actual.get(k) != v}
        if drift:
            log.warning(
                "chroma_hnsw_drift",
                drift=drift,
                hint=(
                    "La collection existante conserve sa metadata d'origine. "
                    "Pour appliquer la nouvelle config HNSW, supprime et régénère "
                    "data/chroma/ (la mémoire sera repartie de zéro)."
                ),
            )

    async def store(self, original_message: str, memory_content: str) -> None:
        """Embed le résumé factuel et le persiste dans ChromaDB."""
        vector = await self._embedder.embed(memory_content)
        entry_id = uuid.uuid4().hex
        metadata: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "original_message": original_message,
        }
        await asyncio.to_thread(
            self._collection.add,
            ids=[entry_id],
            embeddings=[vector],  # type: ignore[arg-type, unused-ignore]
            documents=[memory_content],
            metadatas=[metadata],
        )
        log.info("memory_stored", entry_id=entry_id, preview=memory_content[:80])

    async def store_depot(
        self,
        content: str,
        thought_id: int,
        thought_kind: str | None,
    ) -> None:
        """Indexe un dépôt cognitif (intent `depot`) dans ChromaDB.

        Le tag `kind="depot"` permettra une future détection de boucles
        (clustering sémantique sur les dépôts récurrents). `thought_id`
        pointe vers la ligne SQLite correspondante pour récupérer le
        contexte complet (created_at, processed_at).
        """
        vector = await self._embedder.embed(content)
        entry_id = uuid.uuid4().hex
        metadata: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "kind": "depot",
            "thought_id": thought_id,
            "thought_kind": thought_kind or "none",
        }
        await asyncio.to_thread(
            self._collection.add,
            ids=[entry_id],
            embeddings=[vector],  # type: ignore[arg-type, unused-ignore]
            documents=[content],
            metadatas=[metadata],
        )
        log.info(
            "depot_stored",
            entry_id=entry_id,
            thought_id=thought_id,
            thought_kind=thought_kind,
            preview=content[:80],
        )

    async def store_many(self, items: Sequence[tuple[str, str]]) -> None:
        """Batch-embed et persiste plusieurs (original_message, memory_content).

        Les embeddings sont calculés en parallèle (`asyncio.gather`) puis
        une seule insertion ChromaDB est effectuée, ce qui évite N round-trips
        filesystem sur le volume Pi pour un import initial ou un digest
        hebdomadaire. Sans `items`, no-op.
        """
        if not items:
            return
        contents = [content for _, content in items]
        vectors = await self._embedder.embed_many(contents)
        now_iso = datetime.now(UTC).isoformat()
        ids = [uuid.uuid4().hex for _ in items]
        metadatas: list[dict[str, Any]] = [
            {"timestamp": now_iso, "original_message": original} for original, _ in items
        ]
        await asyncio.to_thread(
            self._collection.add,
            ids=ids,
            embeddings=vectors,  # type: ignore[arg-type, unused-ignore]
            documents=contents,
            metadatas=metadatas,  # type: ignore[arg-type, unused-ignore]
        )
        log.info("memory_stored_batch", count=len(items))

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed des textes arbitraires (parallélisé, borné par le sémaphore).

        Expose l'embedder pour des rapprochements sémantiques ad hoc hors
        ChromaDB (ex. souci ↔ titre d'évènement dans la card « Pour toi »,
        où les titres ne sont pas indexés). Sans textes → liste vide.
        """
        return await self._embedder.embed_many(texts)

    async def find_similar_depots(
        self,
        content: str,
        *,
        top_k: int = 8,
        max_distance: float,
    ) -> list[DepotMatch]:
        """Recherche les dépôts cognitifs sémantiquement proches de `content`.

        Query filtrée `where={"kind": "depot"}`, résultats triés par distance
        cosine croissante et bornés par `max_distance`. Les matchs dont la
        metadata `thought_id` est absente ou invalide (désynchronisation
        SQLite ↔ ChromaDB) sont ignorés avec un log `debug`.
        """
        vector = await self._embedder.embed(content)
        result = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=[vector],  # type: ignore[arg-type, unused-ignore]
            n_results=top_k,
            where={"kind": "depot"},
            include=[IncludeEnum.documents, IncludeEnum.metadatas, IncludeEnum.distances],
        )
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        matches: list[DepotMatch] = []
        for doc, meta, distance in zip(documents, metadatas, distances, strict=True):
            if distance > max_distance:
                continue
            thought_id = (meta or {}).get("thought_id")
            if not isinstance(thought_id, int) or isinstance(thought_id, bool):
                log.debug("depot_match_orphan", preview=doc[:80], thought_id=thought_id)
                continue
            matches.append(DepotMatch(thought_id=thought_id, content=doc, distance=distance))
        matches.sort(key=lambda m: m.distance)
        return matches

    async def retrieve_context(self, query: str, top_k: int = 5) -> list[str]:
        """Retourne les top_k documents pertinents, seuil + boost de récence.

        Sur-échantillonne (`RAG_OVERSAMPLE * top_k`), écarte les souvenirs
        hors-sujet (`distance > rag_max_distance`), puis re-classe par un score
        combinant pertinence sémantique et fraîcheur : `distance - bonus`, où
        `bonus = RAG_RECENCY_MAX_BONUS * 0.5 ** (âge / demi-vie)`. À pertinence
        égale, le souvenir le plus récent passe devant ; un souvenir daté ne
        remonte jamais devant un match sémantique franchement meilleur. La
        signature reste inchangée (retour `list[str]`) : la pondération est
        transparente pour les appelants.
        """
        vector = await self._embedder.embed(query)
        result = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=[vector],  # type: ignore[arg-type, unused-ignore]
            n_results=max(top_k * RAG_OVERSAMPLE, top_k),
            include=[IncludeEnum.documents, IncludeEnum.metadatas, IncludeEnum.distances],
        )
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        now = datetime.now(UTC)
        scored: list[tuple[float, str]] = []
        for doc, meta, distance in zip(documents, metadatas, distances, strict=True):
            if not isinstance(doc, str) or distance > self._rag_max_distance:
                continue
            timestamp = (meta or {}).get("timestamp")
            score = distance - RAG_RECENCY_MAX_BONUS * self._recency_weight(timestamp, now)
            scored.append((score, doc))
        scored.sort(key=lambda pair: pair[0])
        return [doc for _, doc in scored[:top_k]]

    def _recency_weight(self, timestamp: Any, now: datetime) -> float:
        """Poids de récence ∈ [0, 1] à partir d'un `timestamp` ISO (1 = frais).

        Décroissance exponentielle de demi-vie `rag_recency_half_life_days`.
        Timestamp absent/illisible ou demi-vie non positive → 0 (aucun bonus).
        """
        if not isinstance(timestamp, str) or self._rag_recency_half_life_days <= 0:
            return 0.0
        try:
            reference = datetime.fromisoformat(timestamp)
        except ValueError:
            return 0.0
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        age_days = max((now - reference).total_seconds() / 86400.0, 0.0)
        return float(0.5 ** (age_days / self._rag_recency_half_life_days))
