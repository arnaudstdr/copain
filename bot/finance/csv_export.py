"""Sérialisation CSV des écritures budgétaires (`expenses`).

Format FR pensé pour s'ouvrir directement dans Numbers/Excel locale FR sans
dialogue d'import : séparateur `;`, virgule décimale, dates `JJ/MM/AAAA`,
UTF-8 préfixé par un BOM (sinon Excel/Numbers FR tombent en latin-1 sur les
labels accentués).

Le signe du montant porte le sens : `income` → positif, autres kinds
(`punctual`, `recurring_tick`, `saving_tick`) → négatif. Permet une simple
`SOMME` dans le tableur pour obtenir le restant.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from bot.finance.models import Expense

_HEADER = ("date", "type", "libelle", "categorie", "recurring_key", "montant_eur")
_BOM = "﻿"


def _format_amount(kind: str, amount_cents: int) -> str:
    signed = amount_cents if kind == "income" else -amount_cents
    return f"{signed / 100:.2f}".replace(".", ",")


def build_expenses_csv(rows: Sequence[Expense]) -> str:
    """Sérialise une séquence d'`Expense` en CSV FR (avec BOM).

    L'en-tête est toujours présent, même quand `rows` est vide, pour que
    l'utilisateur récupère un fichier exploitable plutôt qu'un blob nu.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(_HEADER)
    for row in rows:
        writer.writerow(
            (
                row.occurred_on.strftime("%d/%m/%Y"),
                row.kind,
                row.label,
                row.category or "",
                row.recurring_key or "",
                _format_amount(row.kind, row.amount_cents),
            )
        )
    return _BOM + buf.getvalue()
