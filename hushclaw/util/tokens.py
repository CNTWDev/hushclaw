"""Token count estimation — CJK-aware heuristic."""
from __future__ import annotations

import json
import unicodedata


def _count_cjk(text: str) -> int:
    """Count CJK (Chinese/Japanese/Korean) characters in *text*."""
    count = 0
    for ch in text:
        cat = unicodedata.category(ch)
        cp = ord(ch)
        # CJK Unified Ideographs and extensions, Hangul, Katakana, Hiragana, etc.
        if (
            0x4E00 <= cp <= 0x9FFF   # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF  # CJK Extension A
            or 0x20000 <= cp <= 0x2A6DF  # CJK Extension B
            or 0xF900 <= cp <= 0xFAFF   # CJK Compatibility Ideographs
            or 0xAC00 <= cp <= 0xD7AF   # Hangul Syllables
            or 0x3040 <= cp <= 0x309F   # Hiragana
            or 0x30A0 <= cp <= 0x30FF   # Katakana
        ):
            count += 1
    return count


def estimate_tokens(text: str) -> int:
    """
    Estimate token count with CJK correction.

    - Latin/ASCII text: ~4 chars per token
    - CJK characters: ~1.5 chars per token (each char is usually 1-2 tokens)

    Falls back to optional tiktoken if installed.
    """
    if not text:
        return 1

    try:
        import tiktoken  # type: ignore[import]
        enc = tiktoken.get_encoding("cl100k_base")
        return max(1, len(enc.encode(text)))
    except ImportError:
        pass
    except Exception:
        # tiktoken may be installed but unable to fetch its encoding assets in
        # offline or sandboxed environments; keep token estimation available.
        pass

    cjk = _count_cjk(text)
    latin = len(text) - cjk
    # CJK: 1.5 chars/token (approx 0.67 tokens per char)
    # Latin: 4 chars/token
    tokens = latin / 4 + cjk / 1.5
    return max(1, int(tokens))


def estimate_messages_tokens(messages: list) -> int:
    """Estimate total tokens for a list of messages."""
    total = 0
    for m in messages:
        if hasattr(m, "content"):
            content = m.content
        elif isinstance(m, dict):
            content = m.get("content", "")
        else:
            content = str(m)
        if isinstance(content, list):
            content = json.dumps(content)
        total += estimate_tokens(str(content)) + 4  # per-message overhead
    return total
