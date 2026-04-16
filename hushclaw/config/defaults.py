"""Schema-derived default configuration snapshot.

This module exists for compatibility with callers that want a plain ``dict`` of
defaults. The canonical source of truth is ``hushclaw.config.schema.Config``;
``DEFAULTS`` is derived from it so values do not drift from the dataclass
defaults used by ``load_config()``.
"""
from __future__ import annotations

from dataclasses import asdict

from hushclaw.config.schema import Config


DEFAULTS: dict = asdict(Config())
