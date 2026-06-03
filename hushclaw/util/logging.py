"""Structured stderr logging — no loguru, no structlog."""
from __future__ import annotations

import json
import logging
import sys
import time
from collections import deque
from threading import RLock


_LOG_BUFFER_MAX = 1500
_LOG_BUFFER: deque[dict] = deque(maxlen=_LOG_BUFFER_MAX)
_LOG_BUFFER_LOCK = RLock()


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


class _RingBufferHandler(logging.Handler):
    """Keep recent HushClaw logs in memory for the personal WebUI."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            item = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                item["exc"] = self.formatException(record.exc_info)
            with _LOG_BUFFER_LOCK:
                _LOG_BUFFER.append(item)
        except Exception:
            self.handleError(record)


def setup_logging(level: str = "WARNING", fmt: str = "text") -> None:
    """Configure root logger for HushClaw."""
    root = logging.getLogger("hushclaw")
    root.setLevel(getattr(logging, level.upper(), logging.WARNING))
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JSONFormatter() if fmt == "json" else _TextFormatter())
    root.addHandler(handler)
    root.addHandler(_RingBufferHandler())
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"hushclaw.{name}")


def recent_logs(limit: int = 300, level: str = "", query: str = "") -> list[dict]:
    """Return recent in-process logs newest last, optionally filtered."""
    try:
        limit = max(1, min(int(limit or 300), _LOG_BUFFER_MAX))
    except Exception:
        limit = 300
    min_level = str(level or "").strip().upper()
    min_levelno = logging._nameToLevel.get(min_level, 0) if min_level else 0
    q = str(query or "").strip().lower()
    with _LOG_BUFFER_LOCK:
        items = list(_LOG_BUFFER)
    if min_levelno:
        items = [item for item in items if logging._nameToLevel.get(str(item.get("level", "")).upper(), 0) >= min_levelno]
    if q:
        items = [
            item for item in items
            if q in str(item.get("message", "")).lower() or q in str(item.get("logger", "")).lower()
        ]
    return items[-limit:]
