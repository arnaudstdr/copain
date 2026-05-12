"""Tests du loader de profil utilisateur (`bot.profile`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.profile import ProfileError, load_profile


def test_load_profile_missing_returns_unloaded(tmp_path: Path) -> None:
    """Fichier absent → is_loaded=False, raw_yaml vide, pas d'exception."""
    profile = load_profile(tmp_path / "profile.yaml")
    assert profile.is_loaded is False
    assert profile.raw_yaml == ""


def test_load_profile_valid_yaml_returns_raw(tmp_path: Path) -> None:
    path = tmp_path / "profile.yaml"
    content = """\
identity:
  name: Arnaud
  city: Sélestat
work:
  role: dev
"""
    path.write_text(content, encoding="utf-8")
    profile = load_profile(path)
    assert profile.is_loaded is True
    assert "Arnaud" in profile.raw_yaml
    assert "Sélestat" in profile.raw_yaml


def test_load_profile_invalid_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "profile.yaml"
    path.write_text("identity:\n  name: Arnaud\n  - invalide\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(path)


def test_load_profile_top_level_list_raises(tmp_path: Path) -> None:
    """Le top niveau doit être un dict, pas une liste."""
    path = tmp_path / "profile.yaml"
    path.write_text("- un\n- deux\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(path)


def test_load_profile_empty_file_returns_unloaded(tmp_path: Path) -> None:
    """Un fichier vide n'est pas une erreur, juste un profil vide."""
    path = tmp_path / "profile.yaml"
    path.write_text("", encoding="utf-8")
    profile = load_profile(path)
    assert profile.is_loaded is False
    assert profile.raw_yaml == ""


# --- Intégration dans build_system_prompt ----------------------------------


def test_build_system_prompt_omits_section_when_no_profile() -> None:
    from bot.llm.prompt import build_system_prompt
    from bot.profile import UserProfile

    prompt = build_system_prompt(
        memory_context=[],
        recent_history=[],
        current_datetime="lundi à 10:00",
        home_city="Sélestat",
        user_profile=UserProfile(raw_yaml="", is_loaded=False),
    )
    assert "Profil utilisateur" not in prompt


def test_build_system_prompt_injects_profile_when_loaded() -> None:
    from bot.llm.prompt import build_system_prompt
    from bot.profile import UserProfile

    profile = UserProfile(
        raw_yaml="identity:\n  name: Arnaud\n  vehicle: Tesla",
        is_loaded=True,
    )
    prompt = build_system_prompt(
        memory_context=[],
        recent_history=[],
        current_datetime="lundi à 10:00",
        home_city="Sélestat",
        user_profile=profile,
    )
    assert "Profil utilisateur" in prompt
    assert "Arnaud" in prompt
    assert "Tesla" in prompt
    # Le bloc profil doit être positionné avant le bloc mémoire pour que le
    # LLM lise les faits stables avant les souvenirs émergents.
    assert prompt.index("Profil utilisateur") < prompt.index("Contexte mémoire")


# --- voice_mode --------------------------------------------------------------


def test_build_system_prompt_voice_mode_off_by_default() -> None:
    from bot.llm.prompt import build_system_prompt
    from bot.profile import UserProfile

    prompt = build_system_prompt(
        memory_context=[],
        recent_history=[],
        current_datetime="lundi à 10:00",
        home_city="Sélestat",
        user_profile=UserProfile(raw_yaml="", is_loaded=False),
    )
    assert "TU RÉPONDS PAR LA VOIX" not in prompt


def test_build_system_prompt_voice_mode_inserts_tts_preamble() -> None:
    from bot.llm.prompt import build_system_prompt
    from bot.profile import UserProfile

    prompt = build_system_prompt(
        memory_context=[],
        recent_history=[],
        current_datetime="lundi à 10:00",
        home_city="Sélestat",
        user_profile=UserProfile(raw_yaml="", is_loaded=False),
        voice_mode=True,
    )
    assert "TU RÉPONDS PAR LA VOIX" in prompt
    assert "Maximum 2 phrases" in prompt
    # Préambule en tête, avant la présentation habituelle de l'assistant.
    assert prompt.index("TU RÉPONDS PAR LA VOIX") < prompt.index("Tu es l'assistant")
