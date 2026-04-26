"""Config / workspace initialization handlers — extracted from server_impl.py.

Handles three WebSocket messages:
  init_workspace       — create workspace dir and seed default files
  save_config          — deep-merge and persist wizard config to TOML
  save_update_policy   — thin wrapper around save_config for update section
"""
from __future__ import annotations

import json
import logging
import time

from hushclaw.config.writer import dict_to_toml_str

log = logging.getLogger("hushclaw.server.config")


async def handle_init_workspace(ws, data: dict, gateway) -> None:
    """Create workspace directory and seed default SOUL.md/USER.md."""
    from pathlib import Path as _Path
    from hushclaw.config.loader import _bootstrap_workspace

    custom_path = (data.get("path") or "").strip()
    cfg = gateway.base_agent.config

    if custom_path:
        ws_dir = _Path(custom_path).expanduser()
    elif cfg.agent.workspace_dir:
        ws_dir = _Path(cfg.agent.workspace_dir)
    else:
        from hushclaw.config.loader import _data_dir
        ws_dir = _data_dir() / "workspace"

    try:
        _bootstrap_workspace(ws_dir)
        await ws.send(json.dumps({
            "type": "workspace_initialized",
            "ok": True,
            "path": str(ws_dir),
            "soul_md": (ws_dir / "SOUL.md").exists(),
            "user_md": (ws_dir / "USER.md").exists(),
        }))
    except Exception as exc:
        await ws.send(json.dumps({
            "type": "workspace_initialized",
            "ok": False,
            "error": str(exc),
        }))


async def handle_save_config(ws, data: dict, apply_config) -> None:
    """Write wizard-supplied config to the user config TOML file.

    *apply_config* is a zero-argument callable (the server's ``_apply_config``
    method) invoked after the file is written to hot-reload the running agent.
    """
    from hushclaw.config.loader import get_config_dir, _load_toml

    t0 = time.perf_counter()
    save_cid = data.get("save_client_id")
    incoming: dict = data.get("config", {}) or {}
    prov_in = incoming.get("provider") if isinstance(incoming.get("provider"), dict) else {}
    api_key_len = len((prov_in.get("api_key") or "").strip()) if isinstance(prov_in, dict) else 0
    log.info(
        "save_config: begin save_client_id=%r sections=%s provider=%s api_key_len=%d transsion=%s",
        save_cid,
        list(incoming.keys()),
        (prov_in.get("name") if isinstance(prov_in, dict) else None),
        api_key_len,
        bool(incoming.get("transsion")),
    )

    cfg_dir = get_config_dir()
    cfg_file = cfg_dir / "hushclaw.toml"

    try:
        existing: dict = _load_toml(cfg_file)
    except Exception:
        existing = {}
    log.debug(
        "save_config: loaded existing keys save_client_id=%r ms=%.1f",
        save_cid,
        (time.perf_counter() - t0) * 1000,
    )

    # Deep-merge only the sections the wizard touched
    for section in ("provider", "agent", "context", "server", "update", "transsion"):
        if section in incoming and isinstance(incoming[section], dict):
            sec = existing.setdefault(section, {})
            for k, v in incoming[section].items():
                # Strip whitespace from string values (guards against copy-paste
                # trailing newlines in keys — would cause "Missing Authentication header").
                if isinstance(v, str):
                    v = v.strip()
                # Allow clearing provider.base_url explicitly. Other empty
                # strings are treated as "unchanged" wizard fields.
                if k == "base_url":
                    sec[k] = v
                    continue
                if v != "":          # skip empty strings (wizard left blank)
                    sec[k] = v

    # email and calendar: multi-account lists (array-of-tables in TOML)
    for list_key in ("email", "calendar"):
        if list_key not in incoming:
            continue
        val = incoming[list_key]
        if isinstance(val, list):
            # Full replacement — strip whitespace in string fields
            cleaned = []
            for acct in val:
                if isinstance(acct, dict):
                    cleaned.append({k: (v.strip() if isinstance(v, str) else v) for k, v in acct.items()})
            existing[list_key] = cleaned
        elif isinstance(val, dict):
            # Legacy single-account payload — wrap or merge into first slot
            cur = existing.get(list_key)
            cleaned = {k: (v.strip() if isinstance(v, str) else v) for k, v in val.items()}
            if isinstance(cur, list) and cur:
                existing[list_key][0] = {**cur[0], **{k: v for k, v in cleaned.items() if v != ""}}
            else:
                existing[list_key] = [cleaned]

    # Agent section: workspace_dir and cheap_model (save separately to allow clearing)
    if "agent" in incoming and isinstance(incoming["agent"], dict):
        agent_in = incoming["agent"]
        if "workspace_dir" in agent_in:
            existing.setdefault("agent", {})["workspace_dir"] = (
                agent_in["workspace_dir"].strip() if isinstance(agent_in["workspace_dir"], str)
                else agent_in["workspace_dir"]
            )
        if "cheap_model" in agent_in:
            existing.setdefault("agent", {})["cheap_model"] = (
                agent_in["cheap_model"].strip() if isinstance(agent_in["cheap_model"], str)
                else agent_in["cheap_model"]
            )

    # Tools section (user_skill_dir)
    if "tools" in incoming and isinstance(incoming["tools"], dict):
        tools_sec = existing.setdefault("tools", {})
        for k, v in incoming["tools"].items():
            if isinstance(v, str):
                v = v.strip()
            tools_sec[k] = v  # allow empty string to clear user_skill_dir

    # Browser section
    if "browser" in incoming and isinstance(incoming["browser"], dict):
        br_in  = incoming["browser"]
        br_sec = existing.setdefault("browser", {})
        for k, v in br_in.items():
            if k in ("use_user_chrome",):
                # Virtual toggle — not stored; drives remote_debugging_url instead.
                continue
            if isinstance(v, (bool, int)):
                br_sec[k] = v
            elif isinstance(v, str) and v != "":
                br_sec[k] = v
        # If "Use My Chrome" toggle was explicitly turned off, clear the URL.
        if br_in.get("use_user_chrome") is False:
            br_sec["remote_debugging_url"] = ""

    # Connectors — one extra nesting level per platform
    if "connectors" in incoming and isinstance(incoming["connectors"], dict):
        conn_sec = existing.setdefault("connectors", {})
        for platform in ("telegram", "feishu", "discord", "slack", "dingtalk", "wecom"):
            plat_in = incoming["connectors"].get(platform)
            if not isinstance(plat_in, dict):
                continue
            plat_sec = conn_sec.setdefault(platform, {})
            for k, v in plat_in.items():
                if isinstance(v, str):
                    v = v.strip()
                # booleans, ints, and lists always overwrite; empty strings are skipped
                if isinstance(v, (bool, int, list)):
                    plat_sec[k] = v
                elif v != "":
                    plat_sec[k] = v

    # api_keys — free-form dict; empty string values clear an existing key
    if "api_keys" in incoming and isinstance(incoming["api_keys"], dict):
        keys_sec = existing.setdefault("api_keys", {})
        for k, v in incoming["api_keys"].items():
            k = k.strip()
            if not k:
                continue
            if isinstance(v, str):
                v = v.strip()
                if v == "":
                    # Explicit empty string = clear the key
                    keys_sec.pop(k, None)
                else:
                    keys_sec[k] = v
            elif v is not None:
                keys_sec[k] = v

    # workspaces — full array replacement (not deep-merge)
    if "workspaces" in incoming and isinstance(incoming["workspaces"], dict):
        ws_list = incoming["workspaces"].get("list")
        if isinstance(ws_list, list):
            validated = []
            for w in ws_list:
                if isinstance(w, dict) and w.get("name") and w.get("path"):
                    validated.append({
                        "name":        str(w["name"]).strip(),
                        "path":        str(w["path"]).strip(),
                        "description": str(w.get("description", "")).strip(),
                    })
            existing.setdefault("workspaces", {})["list"] = validated

    def _ack_payload(ok: bool, **extra) -> dict:
        out = {
            "type": "config_saved",
            "ok": ok,
            "config_file": str(cfg_file),
            "restart_required": False,
            "save_client_id": save_cid,
        }
        out.update(extra)
        return out

    try:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        t_write = time.perf_counter()
        toml_text = dict_to_toml_str(existing)
        cfg_file.write_text(toml_text, encoding="utf-8")
        log.info(
            "save_config: wrote file save_client_id=%r toml_chars=%d write_ms=%.1f total_ms=%.1f",
            save_cid,
            len(toml_text),
            (time.perf_counter() - t_write) * 1000,
            (time.perf_counter() - t0) * 1000,
        )
        # Ack immediately — apply_config() can take 15s+ on large skill/plugin trees
        # and would make the wizard hit its client-side save timeout.
        t_send = time.perf_counter()
        await ws.send(json.dumps(_ack_payload(True)))
        log.info(
            "save_config: config_saved sent save_client_id=%r send_ms=%.1f total_ms=%.1f",
            save_cid,
            (time.perf_counter() - t_send) * 1000,
            (time.perf_counter() - t0) * 1000,
        )
        try:
            t_apply = time.perf_counter()
            apply_config()
            log.info(
                "save_config: apply_config ok save_client_id=%r apply_ms=%.1f",
                save_cid,
                (time.perf_counter() - t_apply) * 1000,
            )
        except Exception as apply_exc:
            log.error(
                "save_config: file written but reload failed save_client_id=%r: %s",
                save_cid,
                apply_exc,
                exc_info=True,
            )
    except Exception as e:
        log.error("save_config error save_client_id=%r: %s", save_cid, e, exc_info=True)
        try:
            await ws.send(json.dumps(_ack_payload(False, error=str(e))))
        except Exception as send_exc:
            log.error("save_config: failed to send error ack: %s", send_exc)


async def handle_save_update_policy(ws, data: dict, apply_config) -> None:
    """Persist update policy settings from dedicated UI controls."""
    incoming = data.get("config", {}) or {}
    await handle_save_config(ws, {"config": {"update": incoming}}, apply_config)
