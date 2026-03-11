"""Structured stderr logging — no loguru, no structlog."""
from __future__ import annotations

import json
import logging
import sys
import time


class _TextFormatter(logging.Formatter):
    LEVEL_COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        color = self.LEVEL_COLORS.get(record.levelname, "")
        level = f"{color}{record.levelname[0]}{self.RESET}"
        return f"{ts} {level} [{record.name}] {record.getMessage()}"


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False)


def setup_logging(level: str = "WARNING", fmt: str = "text") -> None:
    """Configure root logger for GhostClaw."""
    root = logging.getLogger("ghostclaw")
    root.setLevel(getattr(logging, level.upper(), logging.WARNING))
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JSONFormatter() if fmt == "json" else _TextFormatter())
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ghostclaw.{name}")
