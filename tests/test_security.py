"""Tests du middleware sécurité mono-utilisateur."""

from __future__ import annotations

from unittest.mock import MagicMock

from bot.security import is_allowed


def test_allows_correct_user(mock_update_allowed: MagicMock) -> None:
    assert is_allowed(mock_update_allowed, allowed_user_id=42) is True


def test_denies_wrong_user(mock_update_denied: MagicMock) -> None:
    assert is_allowed(mock_update_denied, allowed_user_id=42) is False


def test_denies_when_user_is_none() -> None:
    update = MagicMock()
    update.effective_user = None
    assert is_allowed(update, allowed_user_id=42) is False


def test_denies_when_id_mismatches_even_by_one(mock_update_allowed: MagicMock) -> None:
    mock_update_allowed.effective_user.id = 43
    assert is_allowed(mock_update_allowed, allowed_user_id=42) is False
