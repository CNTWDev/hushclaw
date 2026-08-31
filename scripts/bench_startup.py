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


def _default_tool_surface(details: dict) -> None:
    from hushclaw.config.schema import ToolsConfig
    from hushclaw.runtime.tool_surface import ToolSurfaceSnapshot
    from hushclaw.tools.registry import ToolRegistry

    config = ToolsConfig()
    registry = ToolRegistry()
    registry.load_builtins(enabled=None, browser_enabled=True)
    registry.apply_profile(config.profile)
    registry.apply_enabled_filter(config.enabled)
    from hushclaw.tools.builtins import agent_tools
    registry.register_module(agent_tools)
    surface = ToolSurfaceSnapshot(
        registry,
        mode=config.discovery_mode,
        schema_budget_tokens=config.schema_budget_tokens,
        eager_tools=config.eager_tools or None,
    )
    details.update(surface.stats.to_perf())
    full = max(1, int(surface.stats.full_schema_tokens))
    details["tool_schema_reduction_pct"] = round(
        (1 - surface.stats.visible_schema_tokens / full) * 100,
        1,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    args = parser.parse_args()

    surface_details: dict = {}
    results = [
        _time("import hushclaw", lambda: _import_module("hushclaw")),
        _time("import hushclaw.loop", lambda: _import_module("hushclaw.loop")),
        _time("import hushclaw.tools.registry", lambda: _import_module("hushclaw.tools.registry")),
        _time("build default tool surface", lambda: _default_tool_surface(surface_details)),
        _time("build CLI help", _cli_help),
    ]
    payload = {
        "python": sys.version.split()[0],
        "results": results,
        "tool_surface": surface_details,
    }
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
        if surface_details:
            print(
                "tool surface: "
                f"{surface_details.get('tool_visible_count', 0)}/"
                f"{surface_details.get('tool_registry_count', 0)} visible, "
                f"{surface_details.get('tool_schema_tokens', 0)} tokens, "
                f"{surface_details.get('tool_schema_reduction_pct', 0)}% reduction"
            )
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
