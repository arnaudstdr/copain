"""Package pipeline : orchestration LLM, routing `<meta>` et side effects.

API publique du package — les consommateurs (`bot.api`, `bot.main`,
`bot.dashboard`, tests) importent depuis `bot.pipeline` directement.
Le détail vit dans les sous-modules (`core`, et à venir : `dates`,
`handlers`, `side_effects` — cf. .claude/plans/pipeline-refacto/SPEC.md).
"""

from bot.pipeline.core import (
    FALLBACK_TEXT,
    MAX_HISTORY,
    BotDeps,
    StreamEvent,
    process_message,
    process_message_stream,
)

__all__ = [
    "FALLBACK_TEXT",
    "MAX_HISTORY",
    "BotDeps",
    "StreamEvent",
    "process_message",
    "process_message_stream",
]
