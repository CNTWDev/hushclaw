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


def _normalize_account_entries(raw_accounts) -> list[dict]:
    """Normalize legacy single-account dicts to a list of account dicts."""
    if isinstance(raw_accounts, list):
        return [item for item in raw_accounts if isinstance(item, dict)]
    if isinstance(raw_accounts, dict) and raw_accounts:
        return [raw_accounts]
    return []


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

    from hushclaw.config.system_prompt import should_reset_persisted_system_prompt
    existing_agent = existing.get("agent", {})
    if isinstance(existing_agent, dict):
        existing_prompt = str(existing_agent.get("system_prompt") or "").strip()
        if should_reset_persisted_system_prompt(existing_prompt):
            existing_agent.pop("system_prompt", None)

    # Deep-merge only the sections the wizard touched
    for section in ("provider", "agent", "context", "memory", "server", "update", "transsion"):
        if section in incoming and isinstance(incoming[section], dict):
            sec = existing.setdefault(section, {})
            for k, v in incoming[section].items():
                # Strip whitespace from string values (guards against copy-paste
                # trailing newlines in keys — would cause "Missing Authentication header").
                if isinstance(v, str):
                    v = v.strip()
                # Allow clearing these fields explicitly (empty string = intentional clear).
                if k in ("base_url", "embed_model"):
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
            # Full replacement — strip whitespace; preserve existing password when
            # the frontend omits it (passwords are never echoed back to the browser).
            old_list = _normalize_account_entries(existing.get(list_key))
            cleaned = []
            for i, acct in enumerate(val):
                if isinstance(acct, dict):
                    clean_acct = {k: (v.strip() if isinstance(v, str) else v) for k, v in acct.items()}
                    if not clean_acct.get("password"):
                        old = old_list[i] if i < len(old_list) and isinstance(old_list[i], dict) else {}
                        old_pwd = old.get("password", "")
                        if old_pwd:
                            clean_acct["password"] = old_pwd
                    cleaned.append(clean_acct)
            existing[list_key] = cleaned
        elif isinstance(val, dict):
            # Legacy single-account payload — wrap or merge into first slot
            old_list = _normalize_account_entries(existing.get(list_key))
            cleaned = {k: (v.strip() if isinstance(v, str) else v) for k, v in val.items()}
            if not cleaned.get("password") and old_list:
                old_pwd = old_list[0].get("password", "")
                if old_pwd:
                    cleaned["password"] = old_pwd
            if old_list:
                existing[list_key] = [{**old_list[0], **{k: v for k, v in cleaned.items() if v != ""}}]
            else:
                existing[list_key] = [cleaned]

    # Agent section: workspace_dir and cheap_model (save separately to allow clearing)
    if "agent" in incoming and isinstance(incoming["agent"], dict):
        agent_in = incoming["agent"]
        agent_sec = existing.setdefault("agent", {})
        if "workspace_dir" in agent_in:
            agent_sec["workspace_dir"] = (
                agent_in["workspace_dir"].strip() if isinstance(agent_in["workspace_dir"], str)
                else agent_in["workspace_dir"]
            )
        if "cheap_model" in agent_in:
            agent_sec["cheap_model"] = (
                agent_in["cheap_model"].strip() if isinstance(agent_in["cheap_model"], str)
                else agent_in["cheap_model"]
            )
        if "system_prompt" in agent_in:
            prompt = agent_in["system_prompt"].strip() if isinstance(agent_in["system_prompt"], str) else ""
            if not prompt or should_reset_persisted_system_prompt(prompt):
                agent_sec.pop("system_prompt", None)
            else:
                agent_sec["system_prompt"] = prompt

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

    # App connectors — outbound app integrations. Secret values are stored in
    # SecretStore and only token_ref/config is persisted to TOML.
    if "app_connectors" in incoming and isinstance(incoming["app_connectors"], dict):
        from hushclaw.secrets import get_secret_store

        app_sec = existing.setdefault("app_connectors", {})
        secrets = get_secret_store()
        broker_base_url = str(incoming["app_connectors"].get("broker_base_url") or "").strip()
        if broker_base_url:
            app_sec["broker_base_url"] = broker_base_url
        gh_in = incoming["app_connectors"].get("github")
        if isinstance(gh_in, dict):
            gh_sec = app_sec.setdefault("github", {})
            client_id_ref = str(gh_in.get("client_id_ref") or gh_sec.get("client_id_ref") or "app_connectors.github.client_id").strip()
            client_secret_ref = str(gh_in.get("client_secret_ref") or gh_sec.get("client_secret_ref") or "app_connectors.github.client_secret").strip()
            token_ref = str(gh_in.get("token_ref") or gh_sec.get("token_ref") or "app_connectors.github.token").strip()
            gh_sec["client_id_ref"] = client_id_ref
            gh_sec["client_secret_ref"] = client_secret_ref
            gh_sec["token_ref"] = token_ref
            gh_sec["auth_mode"] = str(gh_in.get("auth_mode") or gh_sec.get("auth_mode") or "managed").strip()
            gh_sec["auth_type"] = str(gh_in.get("auth_type") or gh_sec.get("auth_type") or "pat").strip()
            for k in ("enabled", "allow_actions"):
                if k in gh_in:
                    gh_sec[k] = bool(gh_in[k])
            if "default_repo" in gh_in:
                gh_sec["default_repo"] = str(gh_in.get("default_repo") or "").strip()
            client_id = str(gh_in.get("client_id") or "").strip()
            if client_id:
                secrets.set(client_id_ref, client_id)
            client_secret = str(gh_in.get("client_secret") or "").strip()
            if client_secret:
                secrets.set(client_secret_ref, client_secret)
            token = str(gh_in.get("token") or "").strip()
            if token:
                secrets.set(token_ref, token)
            if gh_in.get("clear_client_id") is True:
                secrets.delete(client_id_ref)
            if gh_in.get("clear_client_secret") is True:
                secrets.delete(client_secret_ref)
            if gh_in.get("clear_token") is True:
                secrets.delete(token_ref)

        gw_in = incoming["app_connectors"].get("google_workspace")
        if isinstance(gw_in, dict):
            gw_sec = app_sec.setdefault("google_workspace", {})
            refs = {
                "client_id": "client_id_ref",
                "client_secret": "client_secret_ref",
                "access_token": "access_token_ref",
                "refresh_token": "refresh_token_ref",
            }
            for k in ("enabled", "allow_actions"):
                if k in gw_in:
                    gw_sec[k] = bool(gw_in[k])
            gw_sec["auth_mode"] = str(gw_in.get("auth_mode") or gw_sec.get("auth_mode") or "managed").strip()
            gw_sec["auth_type"] = str(gw_in.get("auth_type") or gw_sec.get("auth_type") or "oauth").strip()
            if isinstance(gw_in.get("scopes"), list):
                gw_sec["scopes"] = [str(s).strip() for s in gw_in["scopes"] if str(s).strip()]
            for value_key, ref_key in refs.items():
                ref = str(gw_in.get(ref_key) or gw_sec.get(ref_key) or f"app_connectors.google_workspace.{value_key}").strip()
                gw_sec[ref_key] = ref
                value = str(gw_in.get(value_key) or "").strip()
                if value:
                    secrets.set(ref, value)
                if gw_in.get(f"clear_{value_key}") is True:
                    secrets.delete(ref)

        notion_in = incoming["app_connectors"].get("notion")
        if isinstance(notion_in, dict):
            notion_sec = app_sec.setdefault("notion", {})
            client_id_ref = str(notion_in.get("client_id_ref") or notion_sec.get("client_id_ref") or "app_connectors.notion.client_id").strip()
            client_secret_ref = str(notion_in.get("client_secret_ref") or notion_sec.get("client_secret_ref") or "app_connectors.notion.client_secret").strip()
            token_ref = str(notion_in.get("token_ref") or notion_sec.get("token_ref") or "app_connectors.notion.token").strip()
            notion_sec["client_id_ref"] = client_id_ref
            notion_sec["client_secret_ref"] = client_secret_ref
            notion_sec["token_ref"] = token_ref
            notion_sec["auth_mode"] = str(notion_in.get("auth_mode") or notion_sec.get("auth_mode") or "managed").strip()
            notion_sec["auth_type"] = str(notion_in.get("auth_type") or notion_sec.get("auth_type") or "internal_token").strip()
            if "enabled" in notion_in:
                notion_sec["enabled"] = bool(notion_in["enabled"])
            if "allow_actions" in notion_in:
                notion_sec["allow_actions"] = bool(notion_in["allow_actions"])
            if "workspace_name" in notion_in:
                notion_sec["workspace_name"] = str(notion_in.get("workspace_name") or "").strip()
            token = str(notion_in.get("token") or "").strip()
            if token:
                secrets.set(token_ref, token)
            client_id = str(notion_in.get("client_id") or "").strip()
            if client_id:
                secrets.set(client_id_ref, client_id)
            client_secret = str(notion_in.get("client_secret") or "").strip()
            if client_secret:
                secrets.set(client_secret_ref, client_secret)
            if notion_in.get("clear_client_id") is True:
                secrets.delete(client_id_ref)
            if notion_in.get("clear_client_secret") is True:
                secrets.delete(client_secret_ref)
            if notion_in.get("clear_token") is True:
                secrets.delete(token_ref)

        jira_in = incoming["app_connectors"].get("jira")
        if isinstance(jira_in, dict):
            jira_sec = app_sec.setdefault("jira", {})
            client_id_ref = str(jira_in.get("client_id_ref") or jira_sec.get("client_id_ref") or "app_connectors.jira.client_id").strip()
            client_secret_ref = str(jira_in.get("client_secret_ref") or jira_sec.get("client_secret_ref") or "app_connectors.jira.client_secret").strip()
            token_ref = str(jira_in.get("token_ref") or jira_sec.get("token_ref") or "app_connectors.jira.token").strip()
            access_ref = str(jira_in.get("access_token_ref") or jira_sec.get("access_token_ref") or "app_connectors.jira.access_token").strip()
            refresh_ref = str(jira_in.get("refresh_token_ref") or jira_sec.get("refresh_token_ref") or "app_connectors.jira.refresh_token").strip()
            jira_sec["client_id_ref"] = client_id_ref
            jira_sec["client_secret_ref"] = client_secret_ref
            jira_sec["token_ref"] = token_ref
            jira_sec["access_token_ref"] = access_ref
            jira_sec["refresh_token_ref"] = refresh_ref
            jira_sec["auth_mode"] = str(jira_in.get("auth_mode") or jira_sec.get("auth_mode") or "managed").strip()
            jira_sec["auth_type"] = str(jira_in.get("auth_type") or jira_sec.get("auth_type") or "api_token").strip()
            for k in ("enabled", "allow_actions"):
                if k in jira_in:
                    jira_sec[k] = bool(jira_in[k])
            for k in ("site_url", "email", "cloud_id"):
                if k in jira_in:
                    jira_sec[k] = str(jira_in.get(k) or "").strip()
            if isinstance(jira_in.get("scopes"), list):
                jira_sec["scopes"] = [str(s).strip() for s in jira_in["scopes"] if str(s).strip()]
            client_id = str(jira_in.get("client_id") or "").strip()
            if client_id:
                secrets.set(client_id_ref, client_id)
            client_secret = str(jira_in.get("client_secret") or "").strip()
            if client_secret:
                secrets.set(client_secret_ref, client_secret)
            token = str(jira_in.get("token") or "").strip()
            if token:
                secrets.set(token_ref, token)
            access_token = str(jira_in.get("access_token") or "").strip()
            if access_token:
                secrets.set(access_ref, access_token)
            refresh_token = str(jira_in.get("refresh_token") or "").strip()
            if refresh_token:
                secrets.set(refresh_ref, refresh_token)
            if jira_in.get("clear_client_id") is True:
                secrets.delete(client_id_ref)
            if jira_in.get("clear_client_secret") is True:
                secrets.delete(client_secret_ref)
            if jira_in.get("clear_token") is True:
                secrets.delete(token_ref)
            if jira_in.get("clear_access_token") is True:
                secrets.delete(access_ref)
            if jira_in.get("clear_refresh_token") is True:
                secrets.delete(refresh_ref)

        reddit_in = incoming["app_connectors"].get("reddit")
        if isinstance(reddit_in, dict):
            reddit_sec = app_sec.setdefault("reddit", {})
            client_id_ref = str(reddit_in.get("client_id_ref") or reddit_sec.get("client_id_ref") or "app_connectors.reddit.client_id").strip()
            client_secret_ref = str(reddit_in.get("client_secret_ref") or reddit_sec.get("client_secret_ref") or "app_connectors.reddit.client_secret").strip()
            access_ref = str(reddit_in.get("access_token_ref") or reddit_sec.get("access_token_ref") or "app_connectors.reddit.access_token").strip()
            refresh_ref = str(reddit_in.get("refresh_token_ref") or reddit_sec.get("refresh_token_ref") or "app_connectors.reddit.refresh_token").strip()
            reddit_sec["client_id_ref"] = client_id_ref
            reddit_sec["client_secret_ref"] = client_secret_ref
            reddit_sec["access_token_ref"] = access_ref
            reddit_sec["refresh_token_ref"] = refresh_ref
            reddit_sec["auth_mode"] = str(reddit_in.get("auth_mode") or reddit_sec.get("auth_mode") or "custom").strip()
            reddit_sec["auth_type"] = str(reddit_in.get("auth_type") or reddit_sec.get("auth_type") or "oauth").strip()
            for k in ("enabled", "allow_actions"):
                if k in reddit_in:
                    reddit_sec[k] = bool(reddit_in[k])
            for k in ("user_agent", "default_subreddit"):
                if k in reddit_in:
                    reddit_sec[k] = str(reddit_in.get(k) or "").strip()
            for value_key, ref in (
                ("client_id", client_id_ref),
                ("client_secret", client_secret_ref),
                ("access_token", access_ref),
                ("refresh_token", refresh_ref),
            ):
                value = str(reddit_in.get(value_key) or "").strip()
                if value:
                    secrets.set(ref, value)
                if reddit_in.get(f"clear_{value_key}") is True:
                    secrets.delete(ref)

        x_in = incoming["app_connectors"].get("x")
        if isinstance(x_in, dict):
            x_sec = app_sec.setdefault("x", {})
            client_id_ref = str(x_in.get("client_id_ref") or x_sec.get("client_id_ref") or "app_connectors.x.client_id").strip()
            client_secret_ref = str(x_in.get("client_secret_ref") or x_sec.get("client_secret_ref") or "app_connectors.x.client_secret").strip()
            bearer_ref = str(x_in.get("bearer_token_ref") or x_sec.get("bearer_token_ref") or "app_connectors.x.bearer_token").strip()
            access_ref = str(x_in.get("access_token_ref") or x_sec.get("access_token_ref") or "app_connectors.x.access_token").strip()
            refresh_ref = str(x_in.get("refresh_token_ref") or x_sec.get("refresh_token_ref") or "app_connectors.x.refresh_token").strip()
            x_sec["client_id_ref"] = client_id_ref
            x_sec["client_secret_ref"] = client_secret_ref
            x_sec["bearer_token_ref"] = bearer_ref
            x_sec["access_token_ref"] = access_ref
            x_sec["refresh_token_ref"] = refresh_ref
            x_sec["auth_mode"] = str(x_in.get("auth_mode") or x_sec.get("auth_mode") or "custom").strip()
            x_sec["auth_type"] = str(x_in.get("auth_type") or x_sec.get("auth_type") or "oauth2").strip()
            for k in ("enabled", "allow_actions"):
                if k in x_in:
                    x_sec[k] = bool(x_in[k])
            for value_key, ref in (
                ("client_id", client_id_ref),
                ("client_secret", client_secret_ref),
                ("bearer_token", bearer_ref),
                ("access_token", access_ref),
                ("refresh_token", refresh_ref),
            ):
                value = str(x_in.get(value_key) or "").strip()
                if value:
                    secrets.set(ref, value)
                if x_in.get(f"clear_{value_key}") is True:
                    secrets.delete(ref)

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
