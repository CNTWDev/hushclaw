"""Trajectory collection — append structured turn records to JSONL.

A trajectory is a machine-readable log of every agent turn:
  user_input, tool_calls, assistant_response, token counts, timestamps.

Designed for:
  - Fine-tuning dataset collection
  - Behavioral analysis / evals
  - Replay / debugging

File layout::

    trajectory_dir/
      {session_id}.jsonl    ← one JSON record per line, one file per session

Each record schema::

    {
      "ts":        1714000000.123,   // Unix timestamp (float)
      "turn":      0,                // 0-indexed turn number within session
      "session":   "s-abc123...",
      "user":      "...",            // user input
      "assistant": "...",            // final assistant text
      "tool_calls": [                // list of tool calls in this turn
        {"name": "remember", "input": {...}, "result": "...", "is_error": false}
      ],
      "model":        "claude-sonnet-4-6",
      "input_tokens":  120,
      "output_tokens": 45,
      "rounds":        2,
      "stop_reason":   "end_turn"
    }
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hushclaw.providers.base import ToolCall


class TrajectoryWriter:
    """Appends one JSONL record per turn to ``{trajectory_dir}/{session_id}.jsonl``."""

    def __init__(self, trajectory_dir: Path, session_id: str) -> None:
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        # Sanitize session_id for filename use
        safe_sid = "".join(c if (c.isalnum() or c in "-_") else "_" for c in session_id)
        self._path = trajectory_dir / f"{safe_sid}.jsonl"
        self._turn = 0

    def record(
        self,
        *,
        session_id: str,
        user_input: str,
        assistant_text: str,
        tool_calls: list[dict],
        model: str,
        input_tokens: int,
        output_tokens: int,
        rounds: int,
        stop_reason: str,
    ) -> None:
        """Append one turn record to the JSONL file (non-blocking, best-effort)."""
        record = {
            "ts": time.time(),
            "turn": self._turn,
            "session": session_id,
            "user": user_input,
            "assistant": assistant_text,
            "tool_calls": tool_calls,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "rounds": rounds,
            "stop_reason": stop_reason,
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass  # trajectory write failure must not interrupt the agent turn
        self._turn += 1
