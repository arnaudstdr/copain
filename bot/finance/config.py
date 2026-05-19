"""Lecture de la section `finances` du `data/profile.yaml`.

Convention : on logge un warning et on désactive silencieusement quand la
section est absente ou mal formée — cohérent avec `extract_news_config`.
Crasher au boot serait punitif (positionnement « cerveau d'appoint »).

Une seule exception : les `key` dupliquées dans la liste `recurring`
rendent le pointage ambigu. Là, on raise un `ValueError` explicite pour
forcer l'utilisateur à corriger.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from bot.logging_conf import get_logger

log = get_logger(__name__)

RecurringKind = Literal["expense", "saving"]
_VALID_KINDS: frozenset[str] = frozenset({"expense", "saving"})


class FinanceConfigError(ValueError):
    """Erreur de configuration bloquante (ex : `key` dupliquée)."""


@dataclass(frozen=True, slots=True)
class RecurringItem:
    """Une dépense récurrente déclarée dans `data/profile.yaml`."""

    key: str
    label: str
    amount_cents: int
    day: int  # 1..31 ; cap au dernier jour du mois si trop grand
    kind: RecurringKind
    category: str | None = None


@dataclass(frozen=True, slots=True)
class EnvelopeItem:
    """Une enveloppe budgétaire mensuelle (ex: 200€ d'essence par mois).

    Contrairement à une récurrente, l'enveloppe n'est PAS pointée à un jour
    précis : son montant est déduit du restant prévisionnel dès le 1er du
    mois, et les dépenses ponctuelles ayant cette `category` puisent dedans
    au lieu de venir baisser le restant une seconde fois.
    """

    category: str  # slug exact (matche `Expense.category`)
    label: str
    amount_cents: int


@dataclass(frozen=True, slots=True)
class FinanceConfig:
    """Configuration finance chargée depuis le profil YAML."""

    currency: str
    recurring: tuple[RecurringItem, ...]
    envelopes: tuple[EnvelopeItem, ...] = ()

    @classmethod
    def empty(cls) -> FinanceConfig:
        return cls(currency="EUR", recurring=(), envelopes=())

    def find(self, key: str) -> RecurringItem | None:
        for item in self.recurring:
            if item.key == key:
                return item
        return None

    def find_envelope(self, category: str) -> EnvelopeItem | None:
        target = category.strip().lower()
        for env in self.envelopes:
            if env.category.strip().lower() == target:
                return env
        return None

    @property
    def is_configured(self) -> bool:
        return bool(self.recurring) or bool(self.envelopes)


def extract_finance_config(profile_data: dict[str, Any]) -> FinanceConfig:
    """Construit la `FinanceConfig` à partir du dict YAML parsé.

    Retourne `FinanceConfig.empty()` si la section est absente ou ne décrit
    aucune récurrente valide. Raise `FinanceConfigError` uniquement pour les
    incohérences bloquantes (clés dupliquées).
    """
    section = profile_data.get("finances") or {}
    if not isinstance(section, dict):
        log.warning("finance_config_section_invalid", type=type(section).__name__)
        return FinanceConfig.empty()

    currency = str(section.get("currency") or "EUR").strip() or "EUR"

    raw_recurring = section.get("recurring") or []
    if not isinstance(raw_recurring, list):
        log.warning("finance_config_recurring_invalid", type=type(raw_recurring).__name__)
        return FinanceConfig(currency=currency, recurring=())

    items = tuple(_iter_valid_items(raw_recurring))
    _check_unique_keys(items)

    raw_envelopes = section.get("envelopes") or []
    if not isinstance(raw_envelopes, list):
        log.warning("finance_config_envelopes_invalid", type=type(raw_envelopes).__name__)
        envelopes: tuple[EnvelopeItem, ...] = ()
    else:
        envelopes = tuple(_iter_valid_envelopes(raw_envelopes))
        _check_unique_envelope_categories(envelopes)

    return FinanceConfig(currency=currency, recurring=items, envelopes=envelopes)


def _iter_valid_items(raw: list[Any]) -> Iterable[RecurringItem]:
    for idx, raw_item in enumerate(raw):
        item = _parse_one(raw_item, idx)
        if item is not None:
            yield item


def _parse_one(raw: Any, idx: int) -> RecurringItem | None:
    if not isinstance(raw, dict):
        log.warning("finance_recurring_item_not_dict", index=idx)
        return None

    key = str(raw.get("key") or "").strip()
    label = str(raw.get("label") or "").strip()
    if not key or not label:
        log.warning("finance_recurring_item_missing_key_or_label", index=idx, raw=str(raw)[:120])
        return None

    raw_amount = raw.get("amount")
    if not isinstance(raw_amount, int | float) or raw_amount <= 0:
        log.warning("finance_recurring_item_invalid_amount", index=idx, key=key, raw=raw_amount)
        return None
    amount_cents = round(float(raw_amount) * 100)

    raw_day = raw.get("day")
    if not isinstance(raw_day, int) or raw_day < 1 or raw_day > 31:
        log.warning("finance_recurring_item_invalid_day", index=idx, key=key, raw=raw_day)
        return None

    raw_kind = str(raw.get("kind") or "").strip()
    if raw_kind not in _VALID_KINDS:
        log.warning("finance_recurring_item_invalid_kind", index=idx, key=key, raw=raw_kind)
        return None

    raw_category = raw.get("category")
    category = str(raw_category).strip() if raw_category is not None else None
    if category == "":
        category = None

    return RecurringItem(
        key=key,
        label=label,
        amount_cents=amount_cents,
        day=raw_day,
        kind=raw_kind,  # type: ignore[arg-type]
        category=category,
    )


def _check_unique_keys(items: tuple[RecurringItem, ...]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in items:
        if item.key in seen:
            duplicates.append(item.key)
        seen.add(item.key)
    if duplicates:
        raise FinanceConfigError(
            f"clé(s) dupliquée(s) dans finances.recurring : {sorted(set(duplicates))}"
        )


def _iter_valid_envelopes(raw: list[Any]) -> Iterable[EnvelopeItem]:
    for idx, raw_item in enumerate(raw):
        env = _parse_envelope(raw_item, idx)
        if env is not None:
            yield env


def _parse_envelope(raw: Any, idx: int) -> EnvelopeItem | None:
    if not isinstance(raw, dict):
        log.warning("finance_envelope_item_not_dict", index=idx)
        return None
    category = str(raw.get("category") or "").strip()
    if not category:
        log.warning("finance_envelope_missing_category", index=idx, raw=str(raw)[:120])
        return None
    label = str(raw.get("label") or category).strip()
    raw_amount = raw.get("amount")
    if not isinstance(raw_amount, int | float) or raw_amount <= 0:
        log.warning("finance_envelope_invalid_amount", index=idx, category=category, raw=raw_amount)
        return None
    return EnvelopeItem(
        category=category,
        label=label,
        amount_cents=round(float(raw_amount) * 100),
    )


def _check_unique_envelope_categories(items: tuple[EnvelopeItem, ...]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in items:
        key = item.category.strip().lower()
        if key in seen:
            duplicates.append(item.category)
        seen.add(key)
    if duplicates:
        raise FinanceConfigError(
            f"catégorie(s) dupliquée(s) dans finances.envelopes : {sorted(set(duplicates))}"
        )
