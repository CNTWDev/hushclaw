"""Tool registry: discover, register, and look up tools."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable

from hushclaw.tools.base import ToolDefinition, to_api_schema
from hushclaw.util.logging import get_logger

log = get_logger("tools.registry")

# Tool profiles: named subsets of built-in tool names.
# Applied before the tools.enabled filter so both constraints compose.
# "" (empty) = no preset, controlled by enabled list only.
TOOL_PROFILES: dict[str, list[str]] = {
    "full": [
        # memory
        "remember", "recall", "search_notes",
        # system
        "get_time", "platform_info",
        # file
        "read_file", "write_file", "list_dir", "make_download_url",
        # shell + patch
        "run_shell", "apply_patch",
        # skills
        "remember_skill", "list_skills", "use_skill", "list_my_skills",
        # scheduler
        "schedule_task", "list_scheduled_tasks", "cancel_scheduled_task",
        # todos
        "add_todo", "list_todos", "complete_todo",
        # web fetching
        "fetch_url", "jina_read",
        # browser
        "browser_navigate", "browser_get_content", "browser_click",
        "browser_fill", "browser_submit", "browser_screenshot",
        "browser_evaluate", "browser_close",
        "browser_open_for_user", "browser_wait_for_user",
        "browser_snapshot", "browser_click_ref", "browser_fill_ref",
        "browser_new_tab", "browser_list_tabs", "browser_focus_tab", "browser_close_tab",
        "browser_connect_user_chrome",
        # email / calendar
        "send_email", "list_emails", "read_email",
        "list_calendar_events", "create_calendar_event",
    ],
    "coding": [
        "remember", "recall", "search_notes", "get_time", "platform_info",
        "read_file", "write_file", "list_dir", "apply_patch",
        "run_shell",
        "remember_skill", "list_skills", "use_skill",
        "add_todo", "list_todos", "complete_todo",
    ],
    "messaging": [
        "remember", "recall", "search_notes", "get_time",
        "send_email", "list_emails", "read_email",
        "list_calendar_events", "create_calendar_event",
        "remember_skill", "list_skills",
    ],
    "minimal": [
        "remember", "recall", "get_time",
    ],
}


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
        from hushclaw.tools.builtins import (
            memory_tools, system_tools, file_tools, web_tools,
            shell_tools, skill_tools, scheduler_tools, todo_tools, patch,
        )
        for mod in (
            memory_tools, system_tools, file_tools, web_tools,
            shell_tools, skill_tools, scheduler_tools, todo_tools, patch,
        ):
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

    def apply_profile(self, profile: str) -> None:
        """Restrict registered tools to those listed in TOOL_PROFILES[profile].

        No-op if *profile* is an empty string or not in TOOL_PROFILES.
        Must be called **before** :meth:`apply_enabled_filter` so both
        constraints compose (profile narrows the universe; enabled list
        further restricts it).
        """
        if not profile or profile not in TOOL_PROFILES:
            return
        profile_set = set(TOOL_PROFILES[profile])
        self._tools = {k: v for k, v in self._tools.items() if k in profile_set}
        log.info("Applied tool profile %r: %d tools active", profile, len(self._tools))

    def apply_enabled_filter(self, enabled: list[str] | None) -> None:
        """Keep only built-in tools whose names are in *enabled*.

        Plugin and skill tools are preserved even when an enabled list is set.
        This keeps bundled skills usable out-of-the-box when users rely on the
        default built-in enabled list (which intentionally only enumerates core
        built-ins).
        """
        if enabled is None:
            return
        enabled_set = set(enabled)
        skill_tool_names = set().union(*self._skill_tools.values()) if self._skill_tools else set()
        plugin_tool_names = set(self._plugin_tools)
        preserved = skill_tool_names | plugin_tool_names
        self._tools = {
            k: v for k, v in self._tools.items()
            if (k in enabled_set) or (k in preserved)
        }

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
        return self._tools.get(name) or self._resolve_name(name)

    def _resolve_name(self, name: str) -> ToolDefinition | None:
        """Fuzzy fallback: resolve *name* when the exact key isn't registered.

        Handles two common LLM drift patterns:
        1. Namespace prefix stripped  — LLM calls ``pptx_list_visual_layouts``
           when the tool is registered as ``hushclaw-skill-pptx__pptx_list_visual_layouts``.
        2. Module prefix stripped     — LLM calls ``list_visual_layouts``
           when the tool is registered as ``pptx_list_visual_layouts``
           (or ``hushclaw-skill-pptx__pptx_list_visual_layouts``).

        Tries exact-suffix matches on the registered key's segments:
          - after ``__``  (namespace separator)
          - after the first ``_`` group  (module-style prefix like ``pptx_``)

        Returns the first unambiguous match, or None if 0 or 2+ candidates found.
        """
        candidates: list[ToolDefinition] = []
        for key, td in self._tools.items():
            # Strip namespace prefix (e.g. "hushclaw-skill-pptx__pptx_foo" → "pptx_foo")
            after_ns = key.split("__", 1)[-1]
            if after_ns == name:
                candidates.append(td)
                continue
            # Strip one more prefix segment (e.g. "pptx_foo" → "foo") by dropping up to first "_"
            if "_" in after_ns:
                after_prefix = after_ns[after_ns.index("_") + 1:]
                if after_prefix == name:
                    candidates.append(td)
        if len(candidates) == 1:
            log.debug("Resolved tool name %r → %r via fuzzy match", name, candidates[0].name)
            return candidates[0]
        if len(candidates) > 1:
            log.warning(
                "Ambiguous fuzzy tool name %r matches %s — skipping resolution",
                name, [c.name for c in candidates],
            )
        return None

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def to_api_schemas(self) -> list[dict]:
        """Return list of tool schemas for LLM API call."""
        return [to_api_schema(td) for td in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)
