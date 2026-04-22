"""Modèles pour la compétence `fuel` (géolocalisation et stations-service)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, get_args

FuelType = Literal["gazole", "sp95", "sp98", "e10", "e85", "gplc"]
FUEL_TYPES: frozenset[str] = frozenset(get_args(FuelType))

# Synonymes FR → FuelType canonique. Le LLM est censé normaliser mais les
# utilisateurs (et le modèle sous pression) écrivent souvent « diesel »,
# « 98 », « gpl ». Mapping appliqué côté handler avant validation stricte.
FUEL_SYNONYMS: dict[str, FuelType] = {
    "diesel": "gazole",
    "gasoil": "gazole",
    "gazoil": "gazole",
    "gazole": "gazole",
    "sp95": "sp95",
    "sp-95": "sp95",
    "95": "sp95",
    "sans plomb 95": "sp95",
    "sp98": "sp98",
    "sp-98": "sp98",
    "98": "sp98",
    "sans plomb 98": "sp98",
    "e10": "e10",
    "sp95-e10": "e10",
    "sans plomb e10": "e10",
    "e85": "e85",
    "superethanol": "e85",
    "superéthanol": "e85",
    "gpl": "gplc",
    "gplc": "gplc",
    "gpl-c": "gplc",
}

# Libellés d'affichage FR pour les messages renvoyés à l'utilisateur.
FUEL_LABELS: dict[FuelType, str] = {
    "gazole": "Gazole",
    "sp95": "SP95",
    "sp98": "SP98",
    "e10": "SP95-E10",
    "e85": "E85",
    "gplc": "GPLc",
}


@dataclass(frozen=True, slots=True)
class GeoPoint:
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class FuelStation:
    """Une station-service avec son prix pour le carburant demandé."""

    id: str
    address: str
    city: str
    postal_code: str
    lat: float
    lon: float
    distance_km: float
    fuel_type: FuelType
    price_eur: float
    updated_at: datetime | None


def normalize_fuel_type(raw: str | None) -> FuelType | None:
    """Résout un type de carburant libre (LLM ou user) vers `FuelType` canonique.

    Retourne `None` si la valeur ne matche ni un type direct ni un synonyme
    connu (ex: chaîne vide, « charbon »). L'appelant doit alors renvoyer un
    message d'erreur FR à l'utilisateur.
    """
    if not raw:
        return None
    key = raw.strip().lower()
    if key in FUEL_TYPES:
        return key  # type: ignore[return-value]
    return FUEL_SYNONYMS.get(key)
