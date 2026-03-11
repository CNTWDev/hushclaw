"""Tool registry: discover, register, and look up tools."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable

from ghostclaw.tools.base import ToolDefinition, to_api_schema
from ghostclaw.util.logging import get_logger

log = get_logger("tools.registry")


class ToolRegistry:
    """Central registry for all available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._plugin_tools: set[str] = set()  # names added by load_plugins

    def register(self, fn: Callable) -> None:
        """Register a function that has been decorated with @tool."""
        td: ToolDefinition | None = getattr(fn, "_ghostclaw_tool", None)
        if td is None:
            raise ValueError(f"{fn} is not decorated with @tool")
        self._tools[td.name] = td
        log.debug("Registered tool: %s", td.name)

    def register_module(self, module) -> None:
        """Register all @tool-decorated functions from a module."""
        for attr in dir(module):
            obj = getattr(module, attr)
            if callable(obj) and hasattr(obj, "_ghostclaw_tool"):
                self.register(obj)

    def load_builtins(self, enabled: list[str] | None = None) -> None:
        """Import and register all built-in tools."""
        from ghostclaw.tools.builtins import memory_tools, system_tools, file_tools, web_tools, shell_tools
        for mod in (memory_tools, system_tools, file_tools, web_tools, shell_tools):
            self.register_module(mod)
        if enabled is not None:
            # Only keep enabled tools
            self._tools = {k: v for k, v in self._tools.items() if k in enabled}

    def load_plugins(self, plugin_dir: Path) -> None:
        """Load .py files from plugin_dir as tool plugins."""
        if not plugin_dir.exists():
            return
        for py_file in plugin_dir.glob("*.py"):
            try:
                spec = importlib.util.spec_from_file_location(
                    f"ghostclaw_plugin_{py_file.stem}", py_file
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                before = set(self._tools.keys())
                self.register_module(mod)
                self._plugin_tools.update(set(self._tools.keys()) - before)
                log.info("Loaded plugin: %s", py_file.name)
            except Exception as e:
                log.warning("Failed to load plugin %s: %s", py_file.name, e)

    def reload_plugins(self, plugin_dir: Path) -> int:
        """Remove previously loaded plugin tools and re-scan plugin_dir."""
        for name in list(self._plugin_tools):
            self._tools.pop(name, None)
        self._plugin_tools.clear()
        before = len(self._tools)
        self.load_plugins(plugin_dir)
        return len(self._tools) - before

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def to_api_schemas(self) -> list[dict]:
        """Return list of tool schemas for LLM API call."""
        return [to_api_schema(td) for td in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)
