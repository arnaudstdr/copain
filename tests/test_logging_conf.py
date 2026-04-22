"""Tests de `configure_logging` — handler stdout + fichier rotatif optionnel."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bot.logging_conf import configure_logging, get_logger


def _reset_root_logger() -> None:
    """Nettoie les handlers du root entre les tests (structlog configure global)."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()


def test_configure_logging_without_file_only_stdout() -> None:
    _reset_root_logger()
    configure_logging(env="dev", log_file_path=None)
    try:
        handlers = logging.getLogger().handlers
        assert len(handlers) == 1
        assert isinstance(handlers[0], logging.StreamHandler)
        assert not isinstance(handlers[0], RotatingFileHandler)
    finally:
        _reset_root_logger()


def test_configure_logging_with_file_creates_rotating_handler(tmp_path: Path) -> None:
    _reset_root_logger()
    log_file = tmp_path / "logs" / "bot.log"

    configure_logging(env="prod", log_file_path=log_file)
    try:
        handlers = logging.getLogger().handlers
        rotating = [h for h in handlers if isinstance(h, RotatingFileHandler)]
        assert len(rotating) == 1
        assert Path(rotating[0].baseFilename) == log_file.resolve()
        assert log_file.parent.is_dir()
    finally:
        # Fermer les handlers pour libérer le fichier avant que tmp_path soit nettoyé
        _reset_root_logger()


def test_configure_logging_writes_json_lines_to_file(tmp_path: Path) -> None:
    _reset_root_logger()
    log_file = tmp_path / "bot.log"

    configure_logging(env="prod", log_file_path=log_file)
    try:
        logger = get_logger("test")
        logger.info("hello_world", chat_id=42, preview="salut")

        # Forcer le flush sur tous les handlers avant de relire
        for h in logging.getLogger().handlers:
            h.flush()

        content = log_file.read_text(encoding="utf-8")
        lines = [line for line in content.splitlines() if line.strip()]
        assert any("hello_world" in line for line in lines)

        # Au moins une ligne "hello_world" doit être du JSON valide
        payload = next(json.loads(line) for line in lines if "hello_world" in line)
        assert payload["event"] == "hello_world"
        assert payload["chat_id"] == 42
        assert payload["preview"] == "salut"
        assert payload["level"] == "info"
    finally:
        _reset_root_logger()
