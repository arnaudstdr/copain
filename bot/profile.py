"""Chargement du profil utilisateur YAML (data/profile.yaml).

Le profil est un fichier texte édité à la main qui décrit l'utilisateur
(identité, famille, travail, voiture, routines, préférences). Il est lu
au démarrage et injecté tel quel dans le system prompt à chaque appel
LLM, en plus du RAG mémoire.

Format libre : on stocke le YAML brut dans `UserProfile.raw_yaml` et on
le réinjecte verbatim côté prompt. Pas de mapping structuré côté Python
— c'est volontaire, ça permet d'enrichir le profil sans toucher au code.
On valide juste que le fichier parse en YAML et qu'on a un dict au top
niveau (pour attraper les erreurs de syntaxe avant le premier appel LLM).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from bot.logging_conf import get_logger

log = get_logger(__name__)


class ProfileError(RuntimeError):
    """Levée si le fichier profil existe mais n'est pas un YAML valide."""


@dataclass(frozen=True, slots=True)
class UserProfile:
    raw_yaml: str
    is_loaded: bool


def load_profile(path: Path) -> UserProfile:
    """Charge `data/profile.yaml`. Tolérant à l'absence (log warning + retour vide).

    En cas de YAML invalide ou de top-level non-dict, lève `ProfileError` :
    on préfère crasher au démarrage qu'injecter un profil partiel/cassé dans
    le system prompt.
    """
    if not path.exists():
        log.warning(
            "profile_missing",
            path=str(path),
            hint="copie data/profile.example.yaml en data/profile.yaml et édite-le",
        )
        return UserProfile(raw_yaml="", is_loaded=False)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"Lecture du profil impossible ({path}) : {exc}") from exc

    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProfileError(f"Profil YAML invalide ({path}) : {exc}") from exc

    if parsed is None:
        log.warning("profile_empty", path=str(path))
        return UserProfile(raw_yaml="", is_loaded=False)

    if not isinstance(parsed, dict):
        raise ProfileError(
            f"Profil YAML doit être un objet (dict) au top niveau, reçu : {type(parsed).__name__}"
        )

    log.info("profile_loaded", path=str(path), top_keys=sorted(parsed.keys()))
    return UserProfile(raw_yaml=text.strip(), is_loaded=True)
