"""Lightweight ID generation using hashlib."""
from __future__ import annotations

import hashlib
import os
import time


def make_id(prefix: str = "") -> str:
    """Generate a unique ID from random bytes + timestamp."""
    raw = os.urandom(16) + str(time.time_ns()).encode()
    h = hashlib.sha256(raw).hexdigest()[:24]
    return f"{prefix}{h}" if prefix else h
