"""One-shot configuration/data migrations for clean architecture changes."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from hushclaw.config.loader import get_config_dir, get_data_dir

_ORG_CONTEXT_MARKER = "\n\n<!-- [hushclaw:org-context] -->\n"
_REMOVED_AGENT_FIELDS = {"role", "team", "reports_to"}


def _backup(path: Path) -> None:
    if not path.exists():
        return
    ts = time.strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.agentos-cleanup.{ts}.bak")
    shutil.copy2(path, backup)


def _normalize_tags(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        tag = str(item or "").strip()
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def _strip_org_context(value: Any) -> Any:
    if isinstance(value, str) and _ORG_CONTEXT_MARKER in value:
        return value.split(_ORG_CONTEXT_MARKER)[0]
    return value


def _legacy_employee_payload(agent: dict[str, Any]) -> dict[str, Any] | None:
    name = str(agent.get("name") or "").strip()
    if not name:
        return None
    has_org_data = any(str(agent.get(field) or "").strip() for field in _REMOVED_AGENT_FIELDS)
    has_capabilities = bool(_normalize_tags(agent.get("capabilities")))
    if not has_org_data and not has_capabilities:
        return None
    return {
        "agent_name": name,
        "display_name": name,
        "role": str(agent.get("role") or "specialist"),
        "team": str(agent.get("team") or ""),
        "reports_to": str(agent.get("reports_to") or ""),
        "responsibilities": [],
        "capabilities": _normalize_tags(agent.get("capabilities")),
        "description": str(agent.get("description") or ""),
        "status": "active",
    }


def _merge_employee_payload(existing: dict[str, Any], legacy: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    merged = dict(existing)
    changed = False
    for key, value in legacy.items():
        current = merged.get(key)
        if current in (None, "", []):
            merged[key] = value
            changed = True
    return merged, changed


def _upsert_opc_employee_from_legacy_agent(conn: sqlite3.Connection, agent: dict[str, Any]) -> bool:
    legacy = _legacy_employee_payload(agent)
    if legacy is None:
        return False
    now = int(time.time())
    record_id = f"emp-{legacy['agent_name']}"
    row = conn.execute(
        "SELECT payload_json, created FROM opc_records WHERE record_type='employee' AND record_id=?",
        (record_id,),
    ).fetchone()
    existing = json.loads(row[0] or "{}") if row else {}
    merged, changed = _merge_employee_payload(existing, legacy)
    if row and not changed:
        return False
    created = int((row[1] if row else 0) or existing.get("created") or now)
    item = {
        **merged,
        "id": record_id,
        "created": created,
        "updated": now,
    }
    conn.execute(
        "INSERT OR REPLACE INTO opc_records "
        "(record_type, record_id, payload_json, created, updated) "
        "VALUES ('employee', ?, ?, ?, ?)",
        (record_id, json.dumps(item, ensure_ascii=False), created, now),
    )
    return True


def _ensure_opc_records_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS opc_records ("
        "record_type TEXT NOT NULL, "
        "record_id TEXT NOT NULL, "
        "payload_json TEXT NOT NULL DEFAULT '{}', "
        "created INTEGER NOT NULL, "
        "updated INTEGER NOT NULL, "
        "PRIMARY KEY (record_type, record_id))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS opc_records_type_updated "
        "ON opc_records(record_type, updated DESC)"
    )


def _backfill_opc_employees_from_dynamic_agents(path: Path, db_path: Path | None) -> int:
    if db_path is None or not path.exists():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(raw, list):
        return 0
    db_path.parent.mkdir(parents=True, exist_ok=True)
    changed = 0
    try:
        conn = sqlite3.connect(str(db_path))
        _ensure_opc_records_table(conn)
        for item in raw:
            if isinstance(item, dict) and _upsert_opc_employee_from_legacy_agent(conn, item):
                changed += 1
        conn.commit()
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return changed


def _migrate_agent_dict(agent: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    next_agent = dict(agent)
    changed = False
    if "routing_tags" not in next_agent and "capabilities" in next_agent:
        next_agent["routing_tags"] = _normalize_tags(next_agent.get("capabilities"))
        changed = True
    if "capabilities" in next_agent:
        next_agent.pop("capabilities", None)
        changed = True
    for field in _REMOVED_AGENT_FIELDS:
        if field in next_agent:
            next_agent.pop(field, None)
            changed = True
    if "instructions" in next_agent:
        cleaned = _strip_org_context(next_agent.get("instructions"))
        if cleaned != next_agent.get("instructions"):
            next_agent["instructions"] = cleaned
            changed = True
    return next_agent, changed


def migrate_dynamic_agents_file(path: Path, *, opc_db_path: Path | None = None) -> tuple[bool, int]:
    if not path.exists():
        return False, 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False, 0
    if not isinstance(raw, list):
        return False, 0
    backfilled = _backfill_opc_employees_from_dynamic_agents(path, opc_db_path)
    changed = False
    migrated: list[Any] = []
    for item in raw:
        if isinstance(item, dict):
            next_item, item_changed = _migrate_agent_dict(item)
            migrated.append(next_item)
            changed = changed or item_changed
        else:
            migrated.append(item)
    if changed:
        _backup(path)
        path.write_text(json.dumps(migrated, indent=2, ensure_ascii=False), encoding="utf-8")
    return changed, backfilled


def _workspace_dir_from_config(path: Path) -> Path | None:
    if not path.exists():
        return None
    try:
        import tomllib
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    agent = raw.get("agent", {}) if isinstance(raw, dict) else {}
    workspace = agent.get("workspace_dir") if isinstance(agent, dict) else ""
    if not isinstance(workspace, str) or not workspace.strip():
        return None
    return Path(workspace.strip()).expanduser()


def migrate_config_file(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    changed = False
    in_gateway_agent = False
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[[") and stripped.endswith("]]"):
            in_gateway_agent = stripped == "[[gateway.agents]]"
        if in_gateway_agent:
            key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
            if key in _REMOVED_AGENT_FIELDS:
                changed = True
                continue
            if key == "capabilities":
                line = line.replace("capabilities", "routing_tags", 1)
                changed = True
        out.append(line)

    if changed:
        _backup(path)
        path.write_text("".join(out), encoding="utf-8")
    return changed


def migrate_agentos_agent_schema(
    *,
    config_dir: Path | None = None,
    data_dir: Path | None = None,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    config_dir = config_dir or get_config_dir()
    data_dir = data_dir or get_data_dir()
    paths = {
        "config": config_dir / "hushclaw.toml",
        "data_dynamic_agents": data_dir / "dynamic_agents.json",
    }
    resolved_workspace_dir = workspace_dir or _workspace_dir_from_config(paths["config"])
    if resolved_workspace_dir is not None:
        paths["workspace_dynamic_agents"] = Path(resolved_workspace_dir).expanduser() / "dynamic_agents.json"

    result: dict[str, Any] = {"changed": [], "skipped": [], "opc_employees_backfilled": 0}
    if migrate_config_file(paths["config"]):
        result["changed"].append(str(paths["config"]))
    else:
        result["skipped"].append(str(paths["config"]))

    for key in ("data_dynamic_agents", "workspace_dynamic_agents"):
        path = paths.get(key)
        if path is None:
            continue
        changed, backfilled = migrate_dynamic_agents_file(path, opc_db_path=data_dir / "memory.db")
        result["opc_employees_backfilled"] += backfilled
        if changed:
            result["changed"].append(str(path))
        else:
            result["skipped"].append(str(path))
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    workspace = Path(argv[0]).expanduser() if argv else None
    result = migrate_agentos_agent_schema(workspace_dir=workspace)
    changed = result.get("changed") or []
    if changed:
        print(f"summary|AgentOS agent schema migrated: {len(changed)} file(s)")
        for path in changed:
            print(f"info|Migrated {path}")
    else:
        print("info|AgentOS agent schema already clean")
    backfilled = int(result.get("opc_employees_backfilled") or 0)
    if backfilled:
        print(f"info|Backfilled {backfilled} OPC employee record(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
