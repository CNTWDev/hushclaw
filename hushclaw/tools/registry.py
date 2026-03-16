"""Tool registry: discover, register, and look up tools."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable

from hushclaw.tools.base import ToolDefinition, to_api_schema
from hushclaw.util.logging import get_logger

log = get_logger("tools.registry")


class ToolRegistry:
    """Central registry for all available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._plugin_tools: set[str] = set()  # names added by load_plugins (non-skill)
        self._skill_tools: dict[str, set[str]] = {}  # skill_name → set of tool names

    def register(self, fn: Callable) -> None:
        """Register a function that has been decorated with @tool."""
        td: ToolDefinition | None = getattr(fn, "_hushclaw_tool", None)
        if td is None:
            raise ValueError(f"{fn} is not decorated with @tool")
        self._tools[td.name] = td
        log.debug("Registered tool: %s", td.name)

    def register_module(self, module) -> None:
        """Register all @tool-decorated functions from a module."""
        for attr in dir(module):
            obj = getattr(module, attr)
            if callable(obj) and hasattr(obj, "_hushclaw_tool"):
                self.register(obj)

    def load_builtins(self, enabled: list[str] | None = None,
                      browser_enabled: bool = True) -> None:
        """Import and register all built-in tools."""
        from hushclaw.tools.builtins import memory_tools, system_tools, file_tools, web_tools, shell_tools, skill_tools, scheduler_tools, todo_tools
        for mod in (memory_tools, system_tools, file_tools, web_tools, shell_tools, skill_tools, scheduler_tools, todo_tools):
            self.register_module(mod)
        if browser_enabled:
            try:
                from hushclaw.tools.builtins import browser_tools
                self.register_module(browser_tools)
            except Exception:
                pass

        # Email tools (stdlib — always register; tools self-check cfg.email.enabled)
        try:
            from hushclaw.tools.builtins import email_tools
            self.register_module(email_tools)
        except Exception:
            pass

        # CalDAV calendar tools (optional dep: caldav)
        try:
            from hushclaw.tools.builtins import calendar_tools
            self.register_module(calendar_tools)
        except Exception:
            pass

        # macOS native tools (darwin only)
        import sys as _sys
        if _sys.platform == "darwin":
            try:
                from hushclaw.tools.builtins import macos_tools
                self.register_module(macos_tools)
            except Exception:
                pass

        if enabled is not None:
            # Only keep enabled tools
            self._tools = {k: v for k, v in self._tools.items() if k in enabled}

    def apply_enabled_filter(self, enabled: list[str] | None) -> None:
        """Keep only tools whose names are in *enabled*. No-op if enabled is None."""
        if enabled is None:
            return
        enabled_set = set(enabled)
        self._tools = {k: v for k, v in self._tools.items() if k in enabled_set}

    def load_plugins(self, plugin_dir: Path, namespace: str | None = None) -> None:
        """Load .py files from plugin_dir as tool plugins.

        If *namespace* is given (e.g. a skill directory name), each tool's
        registered name is prefixed with ``{namespace}__`` to avoid collisions
        between skill packages and builtins.  The tools are also tracked under
        ``_skill_tools[namespace]`` so they can be cleanly unregistered later.
        """
        if not plugin_dir.exists():
            return
        for py_file in plugin_dir.glob("*.py"):
            try:
                spec = importlib.util.spec_from_file_location(
                    f"hushclaw_plugin_{py_file.stem}", py_file
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                before = set(self._tools.keys())
                if namespace:
                    # Register with prefixed names; don't mutate the original td
                    for attr in dir(mod):
                        obj = getattr(mod, attr)
                        if callable(obj) and hasattr(obj, "_hushclaw_tool"):
                            orig = obj._hushclaw_tool
                            prefixed_name = f"{namespace}__{orig.name}"
                            namespaced_td = ToolDefinition(
                                name=prefixed_name,
                                description=orig.description,
                                parameters=orig.parameters,
                                fn=orig.fn,
                                is_async=orig.is_async,
                                timeout=orig.timeout,
                            )
                            self._tools[prefixed_name] = namespaced_td
                            log.debug("Registered skill tool: %s", prefixed_name)
                else:
                    self.register_module(mod)
                added = set(self._tools.keys()) - before
                if namespace:
                    self._skill_tools.setdefault(namespace, set()).update(added)
                else:
                    self._plugin_tools.update(added)
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

    def unregister_skill(self, skill_name: str) -> int:
        """Remove all tools registered under *skill_name*. Returns count removed."""
        names = self._skill_tools.pop(skill_name, set())
        for name in names:
            self._tools.pop(name, None)
        return len(names)

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def to_api_schemas(self) -> list[dict]:
        """Return list of tool schemas for LLM API call."""
        return [to_api_schema(td) for td in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)
