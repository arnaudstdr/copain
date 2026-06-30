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


# --- current_location ------------------------------------------------------


def test_build_system_prompt_omits_location_when_none() -> None:
    from bot.llm.prompt import build_system_prompt
    from bot.profile import UserProfile

    prompt = build_system_prompt(
        memory_context=[],
        recent_history=[],
        current_datetime="lundi à 10:00",
        home_city="Sélestat",
        user_profile=UserProfile(raw_yaml="", is_loaded=False),
        current_location=None,
    )
    assert "Localisation actuelle" not in prompt


def test_build_system_prompt_injects_current_location() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from bot.llm.prompt import build_system_prompt
    from bot.locations.presence import LocationPresence
    from bot.profile import UserProfile

    tz = ZoneInfo("Europe/Paris")
    arrived = datetime(2026, 5, 12, 9, 15, tzinfo=tz)
    presence = LocationPresence(place="work", arrived_at=arrived, lat=None, lon=None)
    prompt = build_system_prompt(
        memory_context=[],
        recent_history=[],
        current_datetime="mardi à 10:00",
        home_city="Sélestat",
        user_profile=UserProfile(raw_yaml="", is_loaded=False),
        current_location=presence,
        timezone="Europe/Paris",
    )
    assert "Localisation actuelle" in prompt
    assert "au bureau" in prompt  # mapping "work" → "au bureau"
    assert "09:15" in prompt
    # Le bloc localisation doit être avant le contexte mémoire.
    assert prompt.index("Localisation actuelle") < prompt.index("Contexte mémoire")


def test_build_system_prompt_unknown_place_falls_back_to_raw_label() -> None:
    from datetime import UTC, datetime

    from bot.llm.prompt import build_system_prompt
    from bot.locations.presence import LocationPresence
    from bot.profile import UserProfile

    presence = LocationPresence(
        place="schiltigheim",
        arrived_at=datetime(2026, 5, 12, 14, 0, tzinfo=UTC),
        lat=None,
        lon=None,
    )
    prompt = build_system_prompt(
        memory_context=[],
        recent_history=[],
        current_datetime="mardi à 14:00",
        home_city="Sélestat",
        user_profile=UserProfile(raw_yaml="", is_loaded=False),
        current_location=presence,
    )
    # Pas de mapping connu → on injecte le label brut préfixé.
    assert "schiltigheim" in prompt


# --- open_worries (clôture en langage naturel) -------------------------------


def test_build_system_prompt_omits_open_worries_when_empty() -> None:
    from bot.llm.prompt import build_system_prompt
    from bot.profile import UserProfile

    prompt = build_system_prompt(
        memory_context=[],
        recent_history=[],
        current_datetime="lundi à 10:00",
        home_city="Sélestat",
        user_profile=UserProfile(raw_yaml="", is_loaded=False),
        open_worries=(),
    )
    # Le template statique référence « Soucis ouverts » (règle de routage,
    # few-shot) : on cible le marqueur de SECTION pour tester l'omission.
    assert "--- Soucis ouverts" not in prompt


def test_build_system_prompt_injects_open_worries() -> None:
    from datetime import datetime
    from unittest.mock import MagicMock

    from bot.llm.prompt import build_system_prompt
    from bot.profile import UserProfile

    worry = MagicMock()
    worry.id = 12
    worry.content = "peur pour le contrôle technique"
    # Dates SQLite naïves UTC (pattern du projet).
    worry.created_at = datetime(2026, 5, 28, 10, 0)
    prompt = build_system_prompt(
        memory_context=[],
        recent_history=[],
        current_datetime="lundi à 10:00",
        home_city="Sélestat",
        user_profile=UserProfile(raw_yaml="", is_loaded=False),
        timezone="Europe/Paris",
        open_worries=[worry],
    )
    assert "--- Soucis ouverts" in prompt
    assert "[id 12]" in prompt
    assert "peur pour le contrôle technique" in prompt
    assert "28/05" in prompt
    # Le bloc soucis doit être avant le contexte mémoire (comme les autres injections).
    assert prompt.index("--- Soucis ouverts") < prompt.index("Contexte mémoire")


# --- Enveloppes budgétaires --------------------------------------------------


def test_build_system_prompt_omits_envelopes_when_empty() -> None:
    from bot.llm.prompt import build_system_prompt
    from bot.profile import UserProfile

    prompt = build_system_prompt(
        memory_context=[],
        recent_history=[],
        current_datetime="lundi à 10:00",
        home_city="Sélestat",
        user_profile=UserProfile(raw_yaml="", is_loaded=False),
        envelopes=(),
    )
    # Le template statique référence « Enveloppes budgétaires » dans la consigne
    # image (few-shot) : on cible le marqueur de SECTION pour tester l'omission.
    assert "--- Enveloppes budgétaires" not in prompt


def test_build_system_prompt_injects_envelopes_with_joint_marker() -> None:
    from bot.finance.config import EnvelopeItem
    from bot.llm.prompt import build_system_prompt
    from bot.profile import UserProfile

    envelopes = (
        EnvelopeItem(category="courses", label="Courses", amount_cents=60000),
        EnvelopeItem(
            category="nourriture",
            label="Courses (compte joint)",
            amount_cents=60000,
            shared=True,
        ),
    )
    prompt = build_system_prompt(
        memory_context=[],
        recent_history=[],
        current_datetime="lundi à 10:00",
        home_city="Sélestat",
        user_profile=UserProfile(raw_yaml="", is_loaded=False),
        envelopes=envelopes,
    )
    assert "--- Enveloppes budgétaires" in prompt
    assert "courses (Courses)" in prompt
    assert "nourriture (Courses (compte joint)) (compte joint)" in prompt
    assert prompt.index("--- Enveloppes budgétaires") < prompt.index("Contexte mémoire")
