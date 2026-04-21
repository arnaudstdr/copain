"""Service de proactivité : pousse des notifications non sollicitées avec garde-fous.

L'import du module `models` est fait ici pour que la table `notification_logs`
soit enregistrée dans `Base.metadata` **avant** l'appel à `init_schema()` dans
`bot/main.py`. Sans cet import, SQLAlchemy ne créerait pas la table.
"""

from __future__ import annotations

from bot.proactivity import models as models

__all__ = ["models"]
