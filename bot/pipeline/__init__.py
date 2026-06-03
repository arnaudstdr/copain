"""Package pipeline : orchestration LLM, routing `<meta>` et side effects.

API publique du package — les consommateurs (`bot.api`, `bot.main`,
`bot.dashboard`, tests) importent depuis `bot.pipeline` directement.
Le détail vit dans les sous-modules (`core`, `dates`, `handlers`,
`side_effects`).
"""

from bot.pipeline.core import (
    BotDeps,
    StreamEvent,
    process_message,
    process_message_stream,
)
from bot.pipeline.handlers import FALLBACK_TEXT

__all__ = [
    "FALLBACK_TEXT",
    "BotDeps",
    "StreamEvent",
    "process_message",
    "process_message_stream",
]
