#!/usr/bin/env python3
"""Small local performance baseline for HushClaw cold paths."""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _time(label: str, fn) -> dict:
    started = time.perf_counter()
    ok = True
    error = ""
    try:
        fn()
    except Exception as exc:
        ok = False
        error = f"{type(exc).__name__}: {exc}"
    return {
        "label": label,
        "ok": ok,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "error": error,
    }


def _import_module(name: str) -> None:
    importlib.import_module(name)


def _subprocess(label: str, args: list[str]) -> dict:
    started = time.perf_counter()
    proc = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    return {
        "label": label,
        "ok": proc.returncode == 0,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "returncode": proc.returncode,
        "stderr_tail": proc.stderr[-500:],
    }


def _cli_help() -> None:
    from hushclaw.cli import _build_parser

    _build_parser().format_help()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    args = parser.parse_args()

    results = [
        _time("import hushclaw", lambda: _import_module("hushclaw")),
        _time("import hushclaw.loop", lambda: _import_module("hushclaw.loop")),
        _time("import hushclaw.tools.registry", lambda: _import_module("hushclaw.tools.registry")),
        _time("build CLI help", _cli_help),
    ]
    payload = {"python": sys.version.split()[0], "results": results}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for item in results:
            status = "ok" if item["ok"] else "failed"
            print(f"{item['label']}: {item['elapsed_ms']} ms ({status})")
            if item.get("error"):
                print(f"  {item['error']}")
            if item.get("stderr_tail"):
                print(f"  stderr: {item['stderr_tail'].strip()}")
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
