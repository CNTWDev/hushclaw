"""Measure local memory retrieval without touching the user's database.

Run from the repository root: python3 scripts/benchmark_memory.py --notes 1000
Synthetic latency is a capacity signal, not an answer-quality benchmark.
"""
from __future__ import annotations

import argparse
import statistics
import tempfile
import time
from pathlib import Path

from hushclaw.memory.store import MemoryStore


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


def benchmark(note_count: int, query_count: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="hushclaw-memory-benchmark-") as folder:
        store = MemoryStore(Path(folder), embed_provider="local", database_encryption="off")
        try:
            started = time.perf_counter()
            for index in range(note_count):
                store.remember(
                    f"Project {index:05d} uses storage engine {index % 17:02d} "
                    f"for audit records and retrieval in workspace {index % 13:02d}.",
                    title=f"Project {index:05d} storage decision",
                    scope=f"workspace:{index % 13:02d}",
                    persist_to_disk=False,
                )
            write_seconds = time.perf_counter() - started

            times = []
            for index in range(query_count):
                needle = index * note_count // query_count
                started = time.perf_counter()
                store.search(f"Project {needle:05d} storage", limit=5,
                             scopes=[f"workspace:{needle % 13:02d}"])
                times.append((time.perf_counter() - started) * 1000)
            return {
                "notes": note_count, "queries": query_count,
                "write_seconds": round(write_seconds, 3),
                "search_mean_ms": round(statistics.mean(times), 3),
                "search_p95_ms": round(percentile(times, 0.95), 3),
            }
        finally:
            store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", type=int, default=1000)
    parser.add_argument("--queries", type=int, default=30)
    args = parser.parse_args()
    if args.notes < 1 or args.queries < 1:
        parser.error("--notes and --queries must be positive")
    print(benchmark(args.notes, args.queries))
