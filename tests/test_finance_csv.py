"""Tests de la sérialisation CSV des écritures (`bot.finance.csv_export`)."""

from __future__ import annotations

from datetime import date

from bot.finance.csv_export import build_expenses_csv
from bot.finance.models import Expense


def _make(
    *,
    kind: str,
    amount_cents: int,
    label: str,
    occurred_on: date,
    category: str | None = None,
    recurring_key: str | None = None,
) -> Expense:
    return Expense(
        kind=kind,
        amount_cents=amount_cents,
        label=label,
        category=category,
        recurring_key=recurring_key,
        occurred_on=occurred_on,
    )


def test_empty_returns_bom_and_header_only() -> None:
    csv = build_expenses_csv([])
    assert csv.startswith("﻿")  # BOM UTF-8
    lines = csv.removeprefix("﻿").splitlines()
    assert lines == ["date;type;libelle;categorie;recurring_key;montant_eur"]


def test_income_amount_is_positive() -> None:
    rows = [
        _make(
            kind="income",
            amount_cents=250000,
            label="Salaire mai",
            occurred_on=date(2026, 5, 5),
        )
    ]
    body = build_expenses_csv(rows).removeprefix("﻿")
    assert "05/05/2026;income;Salaire mai;;;2500,00" in body


def test_punctual_amount_is_negative_with_comma_decimal() -> None:
    rows = [
        _make(
            kind="punctual",
            amount_cents=2750,
            label="Pharmacie",
            category="santé",
            occurred_on=date(2026, 5, 18),
        )
    ]
    body = build_expenses_csv(rows).removeprefix("﻿")
    assert "18/05/2026;punctual;Pharmacie;santé;;-27,50" in body


def test_recurring_tick_and_saving_tick_are_negative() -> None:
    rows = [
        _make(
            kind="recurring_tick",
            amount_cents=80000,
            label="Loyer",
            recurring_key="loyer",
            occurred_on=date(2026, 5, 5),
        ),
        _make(
            kind="saving_tick",
            amount_cents=20000,
            label="PEL",
            recurring_key="pel",
            occurred_on=date(2026, 5, 5),
        ),
    ]
    body = build_expenses_csv(rows).removeprefix("﻿")
    assert "05/05/2026;recurring_tick;Loyer;;loyer;-800,00" in body
    assert "05/05/2026;saving_tick;PEL;;pel;-200,00" in body


def test_semicolon_is_separator_not_comma() -> None:
    rows = [
        _make(
            kind="punctual",
            amount_cents=1234,
            label="Cafe, viennoiserie",  # virgule dans le label → doit être protégée
            occurred_on=date(2026, 5, 18),
        )
    ]
    body = build_expenses_csv(rows).removeprefix("﻿")
    # En CSV avec ; comme séparateur, une virgule dans le label n'a pas
    # besoin d'être quotée (QUOTE_MINIMAL). Vérifions que la virgule
    # reste telle quelle, sans casser les colonnes.
    line = next(line for line in body.splitlines() if "Cafe" in line)
    assert line.count(";") == 5
    assert "Cafe, viennoiserie" in line


def test_header_columns_order() -> None:
    body = build_expenses_csv([]).removeprefix("﻿")
    header = body.splitlines()[0]
    assert header.split(";") == [
        "date",
        "type",
        "libelle",
        "categorie",
        "recurring_key",
        "montant_eur",
    ]
