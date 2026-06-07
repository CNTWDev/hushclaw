"""OAuth helpers for built-in App Connectors."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets as _secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from hushclaw.util.logging import get_logger
from hushclaw.util.ssl_context import make_ssl_context


STATE_PREFIX = "app_connectors.oauth_state."
X_AUTH_URL = "https://x.com/i/oauth2/authorize"
X_TOKEN_URL = "https://api.x.com/2/oauth2/token"
log = get_logger("app_connectors.oauth")


@dataclass(frozen=True)
class OAuthStart:
    authorization_url: str
    state: str
    mode: str = "custom"


class OAuthError(RuntimeError):
    pass


def _connector_attr(connector_id: str) -> str:
    aliases = {"google-workspace": "google_workspace"}
    return aliases.get(connector_id, connector_id)


def _state_ref(state: str) -> str:
    return f"{STATE_PREFIX}{state}"


def _json_request(url: str, *, method: str = "GET", headers: dict | None = None, data: dict | None = None) -> dict:
    body = None
    req_headers = dict(headers or {})
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20, context=make_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw}
        msg = payload.get("error_description") or payload.get("error") or payload.get("message") or raw
        raise OAuthError(str(msg)) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OAuthError("OAuth provider returned non-JSON response.") from exc


def _form_request(url: str, *, headers: dict | None = None, data: dict) -> dict:
    req_headers = dict(headers or {})
    req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req_headers.setdefault("Accept", "application/json")
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20, context=make_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw}
        msg = payload.get("error_description") or payload.get("error") or payload.get("message") or raw
        raise OAuthError(str(msg)) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OAuthError("OAuth provider returned non-JSON response.") from exc


def _secret(secrets, ref: str, label: str) -> str:
    value = secrets.get(ref)
    if not value:
        raise OAuthError(f"{label} is not configured.")
    return value


def _callback_url(base_url: str, connector_id: str) -> str:
    return f"{base_url.rstrip('/')}/oauth/app-connectors/{connector_id}/callback"


def _token_refs(connector_id: str, cfg) -> dict:
    if connector_id == "google_workspace":
        return {
            "access_token": cfg.access_token_ref,
            "refresh_token": cfg.refresh_token_ref,
        }
    if connector_id == "jira":
        return {
            "access_token": cfg.access_token_ref,
            "refresh_token": cfg.refresh_token_ref,
        }
    if connector_id == "x":
        return {
            "access_token": cfg.access_token_ref,
            "refresh_token": cfg.refresh_token_ref,
        }
    return {"access_token": cfg.token_ref}


def _token_updates(connector_id: str, payload: dict, cfg, secret_store) -> dict:
    refs = _token_refs(connector_id, cfg)
    for payload_key, ref in refs.items():
        value = str(payload.get(payload_key) or "").strip()
        if value:
            secret_store.set(ref, value)
    default_mode = "managed" if connector_id == "x" else str(getattr(cfg, "auth_mode", "managed") or "managed")
    updates = {
        "enabled": True,
        "auth_mode": str(payload.get("auth_mode") or default_mode),
        "auth_type": str(payload.get("auth_type") or "oauth"),
    }
    for key in ("workspace_name", "site_url", "cloud_id", "default_repo"):
        value = str(payload.get(key) or "").strip()
        if value:
            updates[key] = value
    return updates


def _begin_managed_oauth(connector_id: str, config, secret_store, base_url: str, cfg) -> OAuthStart:
    broker_base = str(getattr(config.app_connectors, "broker_base_url", "") or "").strip().rstrip("/")
    if not broker_base:
        raise OAuthError("HushClaw OAuth broker is not configured.")
    redirect_uri = _callback_url(base_url, connector_id)
    state = _secrets.token_urlsafe(32)
    secret_store.set(_state_ref(state), json.dumps({
        "connector": connector_id,
        "redirect_uri": redirect_uri,
        "mode": "managed",
        "created": int(time.time()),
    }))
    payload = _json_request(f"{broker_base}/{connector_id}/start", method="POST", data={
        "connector": connector_id,
        "state": state,
        "redirect_uri": redirect_uri,
        "scopes": getattr(cfg, "scopes", []) or (
            ["tweet.read", "tweet.write", "users.read", "offline.access"]
            if connector_id == "x" else []
        ),
    })
    url = str(payload.get("authorization_url") or payload.get("url") or "").strip()
    if not url:
        detail = payload.get("error_description") or payload.get("error") or payload.get("message") or payload
        raise OAuthError(f"OAuth broker did not return an authorization URL: {detail}")
    return OAuthStart(url, state, "managed")


def _complete_managed_oauth(connector_id: str, state: str, code: str, config, secret_store, cfg) -> dict:
    broker_base = str(getattr(config.app_connectors, "broker_base_url", "") or "").strip().rstrip("/")
    if not broker_base:
        raise OAuthError("HushClaw OAuth broker is not configured.")
    payload = _json_request(f"{broker_base}/{connector_id}/handoff/exchange", method="POST", data={
        "connector": connector_id,
        "handoff_code": code,
        "state": state,
    })
    return _token_updates(connector_id, payload, cfg, secret_store)


def begin_oauth(connector_id: str, config, secret_store, base_url: str) -> OAuthStart:
    connector_id = _connector_attr(connector_id)
    cfg = getattr(config.app_connectors, connector_id, None)
    if cfg is None:
        raise OAuthError(f"Unknown app connector: {connector_id}")
    auth_mode = str(getattr(cfg, "auth_mode", "managed") or "managed").strip()
    if auth_mode == "managed":
        return _begin_managed_oauth(connector_id, config, secret_store, base_url, cfg)
    if auth_mode not in ("custom", "public_client"):
        raise OAuthError(f"Unsupported OAuth mode: {auth_mode}")

    redirect_uri = _callback_url(base_url, connector_id)
    state = _secrets.token_urlsafe(32)
    secret_store.set(_state_ref(state), json.dumps({
        "connector": connector_id,
        "redirect_uri": redirect_uri,
        "mode": auth_mode,
        "created": int(time.time()),
    }))

    if connector_id == "x":
        client_id = _secret(secret_store, getattr(cfg, "oauth_client_id_ref", ""), "X OAuth 2.0 client ID")
        verifier = base64.urlsafe_b64encode(_secrets.token_bytes(32)).decode("ascii").rstrip("=")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        state_payload = {
            "connector": connector_id,
            "redirect_uri": redirect_uri,
            "mode": auth_mode,
            "created": int(time.time()),
            "code_verifier": verifier,
        }
        secret_store.set(_state_ref(state), json.dumps(state_payload))
        scopes = " ".join(getattr(cfg, "scopes", []) or ["tweet.read", "tweet.write", "users.read", "offline.access"])
        log.info(
            "Starting X OAuth 2.0 PKCE: redirect_uri=%s scopes=%s client_id_suffix=%s",
            redirect_uri,
            scopes,
            client_id[-6:] if len(client_id) >= 6 else "set",
        )
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return OAuthStart(X_AUTH_URL + "?" + urllib.parse.urlencode(params), state, auth_mode)

    if connector_id == "google_workspace":
        client_id = _secret(secret_store, cfg.client_id_ref, "Google OAuth client ID")
        scopes = " ".join(getattr(cfg, "scopes", []) or [])
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
        return OAuthStart(url, state, auth_mode)

    if connector_id == "notion":
        client_id = _secret(secret_store, cfg.client_id_ref, "Notion OAuth client ID")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "owner": "user",
            "state": state,
        }
        url = "https://api.notion.com/v1/oauth/authorize?" + urllib.parse.urlencode(params)
        return OAuthStart(url, state, auth_mode)

    if connector_id == "jira":
        client_id = _secret(secret_store, cfg.client_id_ref, "Atlassian OAuth client ID")
        scopes = " ".join(getattr(cfg, "scopes", []) or []) or "read:jira-work read:jira-user offline_access"
        params = {
            "audience": "api.atlassian.com",
            "client_id": client_id,
            "scope": scopes,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "prompt": "consent",
        }
        url = "https://auth.atlassian.com/authorize?" + urllib.parse.urlencode(params)
        return OAuthStart(url, state, auth_mode)

    if connector_id == "github":
        client_id = _secret(secret_store, cfg.client_id_ref, "GitHub OAuth client ID")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "repo read:user",
            "state": state,
        }
        url = "https://github.com/login/oauth/authorize?" + urllib.parse.urlencode(params)
        return OAuthStart(url, state, auth_mode)

    raise OAuthError(f"OAuth is not supported for {connector_id}.")


def complete_oauth(connector_id: str, code: str, state: str, config, secret_store) -> dict:
    connector_id = _connector_attr(connector_id)
    raw_state = secret_store.get(_state_ref(state))
    if not raw_state:
        raise OAuthError("OAuth state is missing or expired.")
    secret_store.delete(_state_ref(state))
    try:
        state_payload = json.loads(raw_state)
    except json.JSONDecodeError as exc:
        raise OAuthError("OAuth state is invalid.") from exc
    if state_payload.get("connector") != connector_id:
        raise OAuthError("OAuth state does not match this connector.")
    mode = str(state_payload.get("mode") or "custom")

    cfg = getattr(config.app_connectors, connector_id, None)
    if cfg is None:
        raise OAuthError(f"Unknown app connector: {connector_id}")
    redirect_uri = state_payload.get("redirect_uri") or ""
    if not redirect_uri:
        raise OAuthError("OAuth redirect URI is missing.")
    if mode == "managed":
        return _complete_managed_oauth(connector_id, state, code, config, secret_store, cfg)

    if connector_id == "x":
        verifier = str(state_payload.get("code_verifier") or "")
        if not verifier:
            raise OAuthError("OAuth PKCE verifier is missing.")
        client_id = _secret(secret_store, getattr(cfg, "oauth_client_id_ref", ""), "X OAuth 2.0 client ID")
        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        }
        client_secret_ref = getattr(cfg, "oauth_client_secret_ref", "")
        client_secret = secret_store.get(client_secret_ref) if client_secret_ref else ""
        headers = None
        if client_secret:
            basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
            headers = {"Authorization": f"Basic {basic}"}
        payload = _form_request(X_TOKEN_URL, headers=headers, data=token_data)
        access = payload.get("access_token", "")
        refresh = payload.get("refresh_token", "")
        if access:
            secret_store.set(cfg.access_token_ref, access)
        if refresh:
            secret_store.set(cfg.refresh_token_ref, refresh)
        return {"enabled": True, "auth_mode": mode, "auth_type": "oauth2_user"}

    if connector_id == "google_workspace":
        payload = _form_request("https://oauth2.googleapis.com/token", data={
            "client_id": _secret(secret_store, cfg.client_id_ref, "Google OAuth client ID"),
            "client_secret": _secret(secret_store, cfg.client_secret_ref, "Google OAuth client secret"),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        })
        access = payload.get("access_token", "")
        refresh = payload.get("refresh_token", "")
        if access:
            secret_store.set(cfg.access_token_ref, access)
        if refresh:
            secret_store.set(cfg.refresh_token_ref, refresh)
        return {"enabled": True, "auth_mode": mode, "auth_type": "oauth"}

    if connector_id == "notion":
        client_id = _secret(secret_store, cfg.client_id_ref, "Notion OAuth client ID")
        client_secret = _secret(secret_store, cfg.client_secret_ref, "Notion OAuth client secret")
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        payload = _json_request(
            "https://api.notion.com/v1/oauth/token",
            method="POST",
            headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        )
        token = payload.get("access_token", "")
        if token:
            secret_store.set(cfg.token_ref, token)
        workspace_name = payload.get("workspace_name") or payload.get("workspace_id") or ""
        return {"enabled": True, "auth_mode": mode, "auth_type": "oauth", "workspace_name": workspace_name}

    if connector_id == "jira":
        payload = _json_request("https://auth.atlassian.com/oauth/token", method="POST", data={
            "grant_type": "authorization_code",
            "client_id": _secret(secret_store, cfg.client_id_ref, "Atlassian OAuth client ID"),
            "client_secret": _secret(secret_store, cfg.client_secret_ref, "Atlassian OAuth client secret"),
            "code": code,
            "redirect_uri": redirect_uri,
        })
        access = payload.get("access_token", "")
        refresh = payload.get("refresh_token", "")
        if access:
            secret_store.set(cfg.access_token_ref, access)
        if refresh and getattr(cfg, "refresh_token_ref", ""):
            secret_store.set(cfg.refresh_token_ref, refresh)
        updates = {"enabled": True, "auth_type": "oauth"}
        if access:
            resources = _json_request(
                "https://api.atlassian.com/oauth/token/accessible-resources",
                headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
            )
            if isinstance(resources, list) and resources:
                first = resources[0]
                updates["cloud_id"] = first.get("id", "")
                updates["site_url"] = first.get("url", "")
        return updates

    if connector_id == "github":
        payload = _form_request(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": _secret(secret_store, cfg.client_id_ref, "GitHub OAuth client ID"),
                "client_secret": _secret(secret_store, cfg.client_secret_ref, "GitHub OAuth client secret"),
                "code": code,
                "redirect_uri": redirect_uri,
                "state": state,
            },
        )
        token = payload.get("access_token", "")
        if token:
            secret_store.set(cfg.token_ref, token)
        return {"enabled": True, "auth_mode": mode, "auth_type": "oauth"}

    raise OAuthError(f"OAuth is not supported for {connector_id}.")


def persist_connector_updates(connector_id: str, updates: dict) -> None:
    from hushclaw.config.loader import get_config_dir, _load_toml
    from hushclaw.config.writer import dict_to_toml_str

    connector_id = _connector_attr(connector_id)
    cfg_file = get_config_dir() / "hushclaw.toml"
    existing = _load_toml(cfg_file)
    app = existing.setdefault("app_connectors", {})
    sec = app.setdefault(connector_id, {})
    for key, value in updates.items():
        if value == "":
            continue
        sec[key] = value
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(dict_to_toml_str(existing), encoding="utf-8")
