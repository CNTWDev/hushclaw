"""TOML config writer — no new deps, handles flat-section schema only."""
from __future__ import annotations

import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Low-level TOML value formatting
# ---------------------------------------------------------------------------

def _toml_value(v) -> str | None:
    """Format a Python value as a TOML value string. Returns None to skip."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(v, Path):
        return _toml_value(str(v))
    if isinstance(v, list):
        items = [_toml_value(item) for item in v]
        items = [i for i in items if i is not None]
        return "[" + ", ".join(items) + "]"
    return None


def _list_val(lst: list) -> str:
    parts = [_toml_value(item) for item in lst]
    return "[" + ", ".join(p for p in parts if p is not None) + "]"


# ---------------------------------------------------------------------------
# Full-featured dict → TOML string (supports subsections + arrays-of-tables)
# ---------------------------------------------------------------------------

def dict_to_toml_str(data: dict) -> str:
    """
    Serialize *data* to a TOML string.

    Supports scalars, scalar lists, ``[section]``, ``[section.subsection]``,
    and ``[[section.array_of_tables]]``.  Suitable for serializing the complete
    HushClaw config dict (including ``[[gateway.agents]]``).
    """
    lines: list[str] = []

    # Top-level scalars and scalar lists
    for k, v in data.items():
        if not isinstance(v, (dict, list)):
            s = _toml_value(v)
            if s is not None:
                lines.append(f"{k} = {s}")
        elif isinstance(v, list) and all(not isinstance(i, dict) for i in v):
            lines.append(f"{k} = {_list_val(v)}")

    # Sections
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        lines.append(f"\n[{k}]")
        # Scalar and scalar-list keys within the section
        for sk, sv in v.items():
            if isinstance(sv, list) and sv and all(isinstance(i, dict) for i in sv):
                pass  # arrays-of-tables handled below
            elif isinstance(sv, dict):
                pass  # subsection handled below
            elif isinstance(sv, list):
                lines.append(f"{sk} = {_list_val(sv)}")
            else:
                s = _toml_value(sv)
                if s is not None:
                    lines.append(f"{sk} = {s}")
        # Subsections [k.sk]
        for sk, sv in v.items():
            if isinstance(sv, dict):
                lines.append(f"\n[{k}.{sk}]")
                for ik, iv in sv.items():
                    if isinstance(iv, list) and all(not isinstance(i, dict) for i in iv):
                        lines.append(f"{ik} = {_list_val(iv)}")
                    elif not isinstance(iv, (dict, list)):
                        s = _toml_value(iv)
                        if s is not None:
                            lines.append(f"{ik} = {s}")
        # Arrays-of-tables [[k.sk]]
        for sk, sv in v.items():
            if isinstance(sv, list) and sv and all(isinstance(i, dict) for i in sv):
                for item in sv:
                    lines.append(f"\n[[{k}.{sk}]]")
                    for ik, iv in item.items():
                        if isinstance(iv, list) and all(not isinstance(i, dict) for i in iv):
                            lines.append(f"{ik} = {_list_val(iv)}")
                        elif not isinstance(iv, (dict, list)):
                            s = _toml_value(iv)
                            if s is not None:
                                lines.append(f"{ik} = {s}")

    # Top-level arrays-of-tables [[k]]  (e.g. [[email]], [[calendar]])
    for k, v in data.items():
        if isinstance(v, list) and v and all(isinstance(i, dict) for i in v):
            for item in v:
                lines.append(f"\n[[{k}]]")
                for ik, iv in item.items():
                    if isinstance(iv, list) and all(not isinstance(i, dict) for i in iv):
                        lines.append(f"{ik} = {_list_val(iv)}")
                    elif not isinstance(iv, (dict, list)):
                        s = _toml_value(iv)
                        if s is not None:
                            lines.append(f"{ik} = {s}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Write a complete config file from a dict-of-dicts (flat sections only)
# ---------------------------------------------------------------------------

def write_config_toml(path: Path, sections: dict[str, dict]) -> None:
    """
    Write a simple flat-section TOML config file.

    Each key in *sections* becomes a ``[section]`` header.
    Values must be flat (str, int, float, bool, list[str], Path, or None).
    None values are skipped. Nested dicts in a section are skipped.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    first = True
    for section, values in sections.items():
        if not isinstance(values, dict):
            continue
        if not first:
            lines.append("")
        first = False
        lines.append(f"[{section}]")
        for k, v in values.items():
            if isinstance(v, dict):
                continue  # skip nested tables
            sv = _toml_value(v)
            if sv is not None:
                lines.append(f"{k} = {sv}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# In-place single-value update
# ---------------------------------------------------------------------------

def _coerce(value: str):
    """Coerce a CLI string value to an appropriate Python type."""
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def set_config_value(path: Path, dotted_key: str, value: str) -> None:
    """
    Update a single ``section.field`` key in an existing TOML file.
    Creates the file with just that section if it doesn't exist.

    Uses regex-based text modification to preserve comments and formatting.
    """
    parts = dotted_key.split(".", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Key must be in 'section.field' format, got: {dotted_key!r}"
        )
    section, field = parts
    coerced = _coerce(value)
    sv = _toml_value(coerced)
    if sv is None:
        raise ValueError(f"Cannot serialize value: {value!r}")

    if not path.exists():
        write_config_toml(path, {section: {field: coerced}})
        return

    content = path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    # Find the target [section] header
    section_pattern = re.compile(r"^\s*\[" + re.escape(section) + r"\]\s*$")
    next_section_pattern = re.compile(r"^\s*\[")

    section_start = None
    for i, line in enumerate(lines):
        if section_pattern.match(line):
            section_start = i
            break

    if section_start is None:
        # Section doesn't exist — append it
        if not content.endswith("\n"):
            lines.append("\n")
        lines.append(f"\n[{section}]\n")
        lines.append(f"{field} = {sv}\n")
        path.write_text("".join(lines), encoding="utf-8")
        return

    # Find section end (next section header or EOF)
    section_end = len(lines)
    for i in range(section_start + 1, len(lines)):
        if next_section_pattern.match(lines[i]) and not lines[i].strip().startswith("#"):
            section_end = i
            break

    # Look for existing key within the section
    key_pattern = re.compile(
        r"^(\s*" + re.escape(field) + r"\s*=\s*)(.+)$"
    )
    updated = False
    for i in range(section_start + 1, section_end):
        m = key_pattern.match(lines[i])
        if m:
            lines[i] = f"{field} = {sv}\n"
            updated = True
            break

    if not updated:
        # Key not found in section — insert before section_end
        lines.insert(section_end, f"{field} = {sv}\n")

    path.write_text("".join(lines), encoding="utf-8")
