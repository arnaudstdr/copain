"""Fixtures partagées pour la suite de tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

SAMPLE_META_JSON = """\
<meta>
{
  "intent": "task",
  "store_memory": true,
  "memory_content": "Arnaud veut arroser les plantes demain.",
  "task": {
    "content": "arroser les plantes",
    "due_str": "demain 18h"
  },
  "search_query": null
}
</meta>
"""

SAMPLE_LLM_RESPONSE = f"D'accord, je te le rappelle demain à 18h.\n{SAMPLE_META_JSON}"


@pytest.fixture
def sample_llm_response() -> str:
    return SAMPLE_LLM_RESPONSE


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Iterator[Path]:
    data = tmp_path / "data"
    data.mkdir()
    yield data


@pytest.fixture
def mock_embedder() -> AsyncMock:
    """Embedder mocké qui renvoie un vecteur déterministe de petite taille."""
    embedder = AsyncMock()
    embedder.embed.return_value = [0.1] * 8
    return embedder


@pytest.fixture
async def chroma_persist_dir(tmp_data_dir: Path) -> AsyncIterator[Path]:
    chroma = tmp_data_dir / "chroma"
    chroma.mkdir()
    yield chroma


@pytest.fixture
def mock_update_allowed() -> MagicMock:
    """Update Telegram factice dont l'user.id matche ALLOWED_USER_ID=42."""
    update = MagicMock()
    update.effective_user.id = 42
    update.effective_user.username = "arnaud"
    return update


@pytest.fixture
def mock_update_denied() -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 9999
    update.effective_user.username = "intrus"
    return update
