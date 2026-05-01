"""ContextPolicy: explicit token budget declaration per prompt section."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class ContextPolicy:
    """Explicit token budget per prompt section."""

    # Stable cache prefix (for Anthropic/OpenAI KV cache — rarely changes)
    stable_budget: int = 1_500

    # Dynamic suffix (per-query fresh content)
    dynamic_budget: int = 2_500

    # Explicit user-selected references. These are separate from memory recall:
    # they represent direct intent in the current turn and have their own cap.
    reference_max_tokens: int = 1_500
    reference_max_items: int = 5
    reference_item_max_tokens: int = 500

    # History budget (conversation turns kept in context)
    history_budget: int = 60_000

    # Compact when history token estimate exceeds this fraction of history_budget
    compact_threshold: float = 0.85

    # Always keep N most recent turns uncompacted
    compact_keep_turns: int = 6

    # "lossless" saves old turns to memory before replacing; "summarize" discards
    compact_strategy: str = "lossless"

    # Skip memories below this relevance score (0.0–1.0)
    memory_min_score: float = 0.25

    # Hard cap on injected memories (in tokens, approx 1 token ≈ 4 chars)
    memory_max_tokens: int = 800

    # Creativity engine — light defaults that improve recall diversity without randomness
    memory_decay_rate: float = 0.002   # half-life ~350 days; set 0.0 to disable decay
    retrieval_temperature: float = 0.1  # softmax temperature; set 0.0 for fully deterministic top-k
    serendipity_budget: float = 0.0
    # Hard age gate: drop notes older than N days from recall. 0 = no limit.
    max_age_days: int = 0
