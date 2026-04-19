"""Config subcommands: show, path, set."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _config_file_path() -> Path:
    from hushclaw.config.loader import get_config_dir
    return get_config_dir() / "hushclaw.toml"


def _toml_value_show(v) -> str | None:
    """Format a value for TOML display (config show)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(v, list):
        if not v:
            return "[]"
        items = [_toml_value_show(i) for i in v]
        items = [i for i in items if i is not None]
        if sum(len(i) for i in items) > 60:
            inner = ",\n    ".join(items)
            return f"[\n    {inner},\n]"
        return "[" + ", ".join(items) + "]"
    return f'"{v}"'


def _config_to_toml_str(config) -> str:
    """Render a Config dataclass as a TOML string for human display."""
    import dataclasses
    d = dataclasses.asdict(config)

    section_order = [
        "agent", "provider", "memory", "tools",
        "logging", "context", "gateway", "server",
    ]
    lines: list[str] = []

    for section in section_order:
        data = d.get(section)
        if not isinstance(data, dict):
            continue

        gateway_agents = []
        gateway_pipelines = {}
        if section == "gateway":
            gateway_agents = data.pop("agents", [])
            gateway_pipelines = data.pop("pipelines", {})

        section_lines = []
        for k, v in data.items():
            if isinstance(v, dict):
                continue
            sv = _toml_value_show(v)
            if sv is not None:
                section_lines.append(f"{k} = {sv}")

        if section_lines:
            lines.append(f"\n[{section}]")
            lines.extend(section_lines)

        if section == "gateway":
            if gateway_pipelines:
                lines.append("")
                lines.append("[gateway.pipelines]")
                for name, agent_list in gateway_pipelines.items():
                    items = ", ".join(f'"{a}"' for a in agent_list)
                    lines.append(f"{name} = [{items}]")
            for ag in gateway_agents:
                lines.append("")
                lines.append("[[gateway.agents]]")
                for k, v in ag.items():
                    if v == "" or v == []:
                        continue
                    sv = _toml_value_show(v)
                    if sv is not None:
                        lines.append(f"{k} = {sv}")

    return "\n".join(lines).lstrip("\n") + "\n"


def cmd_config_show(args) -> int:
    from hushclaw.config.loader import load_config
    config = load_config()
    cfg_path = _config_file_path()

    if getattr(args, "json", False):
        import dataclasses
        print(json.dumps(dataclasses.asdict(config), indent=2, default=str))
        return 0

    exists = "exists" if cfg_path.exists() else "not found"
    print(f"# Active config  ({cfg_path})  [{exists}]")
    print(_config_to_toml_str(config))
    return 0


def cmd_config_path(args) -> int:
    from hushclaw.config.loader import get_config_dir, get_data_dir
    cfg_dir = get_config_dir()
    data_dir = get_data_dir()
    cfg_file = cfg_dir / "hushclaw.toml"
    plugin_dir = cfg_dir / "tools"

    def _status(p: Path) -> str:
        return "[exists]" if p.exists() else "[not found]"

    print(f"Config file: {cfg_file}  {_status(cfg_file)}")
    print(f"Data dir:    {data_dir}/  {_status(data_dir)}")
    print(f"Plugin dir:  {plugin_dir}/  {_status(plugin_dir)}")
    return 0


def cmd_config_set(args) -> int:
    from hushclaw.config.writer import set_config_value
    cfg_path = _config_file_path()
    try:
        set_config_value(cfg_path, args.key, args.value)
        print(f"Set {args.key} = {args.value!r}  ({cfg_path})")
        return 0
    except ValueError as e:
        print(f"[Error] {e}", file=sys.stderr)
        return 1
