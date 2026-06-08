"""Single entry point for built-in and skill-declared credentials."""
from __future__ import annotations

import os
from pathlib import Path
import tomllib

from hushclaw.config.writer import dict_to_toml_str
from hushclaw.secrets.registry import (
    API_KEY_ENV_MAP,
    ApiKeySpec,
    api_key_secret_ref,
    api_key_secret_uri,
    get_api_key_spec,
    is_secret_uri,
    known_api_key_specs,
    resolve_api_key,
    resolve_config_api_key,
    secret_ref_from_uri,
)
from hushclaw.secrets.store import get_secret_store


class CredentialService:
    """Resolve, migrate, persist, and describe tool / skill API keys."""

    def __init__(self, *, secret_store=None, skill_registry=None) -> None:
        self.secret_store = secret_store or get_secret_store()
        self._skill_registry = skill_registry
        self._env_vars_we_set: set[str] = set()

    def set_skill_registry(self, skill_registry) -> None:
        self._skill_registry = skill_registry

    def register_skill_registry(self, skill_registry) -> None:
        self.set_skill_registry(skill_registry)

    def _coerce_spec(self, item) -> ApiKeySpec | None:
        if isinstance(item, ApiKeySpec):
            return item
        if not isinstance(item, dict):
            return None
        key = str(item.get("key") or "").strip()
        if not key:
            return None
        env_vars = item.get("env_vars") or item.get("env") or ()
        aliases = item.get("aliases") or ()
        if isinstance(env_vars, str):
            env_vars = (env_vars,)
        elif isinstance(env_vars, list):
            env_vars = tuple(str(v).strip() for v in env_vars if str(v).strip())
        else:
            env_vars = tuple(env_vars)
        if isinstance(aliases, str):
            aliases = (aliases,)
        elif isinstance(aliases, list):
            aliases = tuple(str(v).strip() for v in aliases if str(v).strip())
        else:
            aliases = tuple(aliases)
        return ApiKeySpec(
            key=key,
            label=str(item.get("label") or key.replace("_", " ")).strip(),
            description=str(item.get("description") or "").strip(),
            env_vars=env_vars,
            aliases=aliases,
            docs_url=str(item.get("docs_url") or item.get("docs") or "").strip(),
            manage_url=str(item.get("manage_url") or item.get("apply_url") or item.get("manage") or "").strip(),
            category=str(item.get("category") or "skill").strip() or "skill",
            supports_apply_link=bool(item.get("supports_apply_link", True)),
        )

    def extra_specs(self) -> list[ApiKeySpec]:
        reg = self._skill_registry
        if reg is None or not hasattr(reg, "credential_specs"):
            return []
        out: list[ApiKeySpec] = []
        for item in reg.credential_specs() or []:
            spec = self._coerce_spec(item)
            if spec is not None:
                out.append(spec)
        return out

    def specs(self) -> list[ApiKeySpec]:
        merged: list[ApiKeySpec] = list(known_api_key_specs())
        by_key = {item.key: idx for idx, item in enumerate(merged)}
        for spec in self.extra_specs():
            idx = by_key.get(spec.key)
            if idx is None:
                by_key[spec.key] = len(merged)
                merged.append(spec)
            else:
                merged[idx] = spec
        return merged

    def resolve(self, key: str, *, api_keys: dict | None = None, prefer_env: bool = True) -> tuple[str, str]:
        if prefer_env:
            return resolve_api_key(api_keys, self.secret_store, key)
        return resolve_config_api_key(api_keys, self.secret_store, key)

    def resolve_config_only(self, key: str, *, api_keys: dict | None = None) -> tuple[str, str]:
        return resolve_config_api_key(api_keys, self.secret_store, key)

    def _normalize_api_keys_map(self, api_keys: dict | None) -> tuple[dict[str, str], bool]:
        normalized: dict[str, str] = {}
        changed = False
        if not isinstance(api_keys, dict):
            return normalized, changed
        for raw_key, raw_value in api_keys.items():
            key = str(raw_key).strip()
            if not key:
                changed = True
                continue
            value = str(raw_value or "").strip()
            if not value:
                changed = True
                continue
            if is_secret_uri(value):
                normalized[key] = value
                continue
            self.secret_store.set(api_key_secret_ref(key), value)
            normalized[key] = api_key_secret_uri(key)
            changed = True
        return normalized, changed

    def migrate_api_keys(self, api_keys: dict | None) -> tuple[dict[str, str], bool]:
        return self._normalize_api_keys_map(api_keys)

    def migrate_config_file(self, cfg_file: Path) -> dict[str, str]:
        try:
            with open(cfg_file, "rb") as f:
                data = tomllib.load(f)
        except FileNotFoundError:
            return {}
        except Exception:
            return {}
        api_keys = data.get("api_keys", {})
        normalized, changed = self._normalize_api_keys_map(api_keys)
        if not changed:
            return normalized or (api_keys if isinstance(api_keys, dict) else {})
        if normalized:
            data["api_keys"] = normalized
        else:
            data.pop("api_keys", None)
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(dict_to_toml_str(data), encoding="utf-8")
        return normalized

    def apply_updates(self, existing: dict, updates: dict | None) -> dict:
        if not isinstance(existing, dict):
            existing = {}
        if not isinstance(updates, dict):
            return existing
        keys_sec = existing.setdefault("api_keys", {})
        for raw_key, raw_value in updates.items():
            key = str(raw_key).strip()
            if not key:
                continue
            value = str(raw_value or "").strip()
            if not value:
                self.secret_store.delete(api_key_secret_ref(key))
                keys_sec.pop(key, None)
                continue
            self.secret_store.set(api_key_secret_ref(key), value)
            keys_sec[key] = api_key_secret_uri(key)
        if not keys_sec:
            existing.pop("api_keys", None)
        return existing

    def status_view(self, api_keys: dict | None = None) -> list[dict]:
        seen: set[str] = set()
        items: list[dict] = []
        spec_list = self.specs()
        for spec in spec_list:
            value, source = self.resolve_config_only(spec.key, api_keys=api_keys)
            if not value:
                value, source = self.resolve(spec.key, api_keys={}, prefer_env=True)
            items.append({
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "env_var": spec.env_vars[0] if spec.env_vars else "",
                "docs_url": spec.docs_url,
                "manage_url": spec.manage_url,
                "category": spec.category,
                "configured": bool(value),
                "source": source,
                "can_clear_saved": source in {"secret_store", "config"},
            })
            seen.add(spec.key)
        if isinstance(api_keys, dict):
            for raw_key in sorted(api_keys.keys()):
                key = str(raw_key).strip()
                if not key or key in seen:
                    continue
                value, source = self.resolve_config_only(key, api_keys=api_keys)
                if not value:
                    value, source = self.resolve(key, api_keys={}, prefer_env=True)
                items.append({
                    "key": key,
                    "label": key.replace("_", " "),
                    "description": "Custom API key for an installed tool or skill.",
                    "env_var": key.upper(),
                    "docs_url": "",
                    "manage_url": "",
                    "category": "custom",
                    "configured": bool(value),
                    "source": source,
                    "can_clear_saved": source in {"secret_store", "config"},
                })
        return items

    def project_env(self, api_keys: dict | None) -> None:
        if not isinstance(api_keys, dict):
            return
        for cfg_key, env_var in API_KEY_ENV_MAP.items():
            value, _source = self.resolve_config_only(cfg_key, api_keys=api_keys)
            existing = os.environ.get(env_var, "")
            if value:
                if not existing or env_var in self._env_vars_we_set:
                    os.environ[env_var] = value
                    self._env_vars_we_set.add(env_var)
            elif env_var in self._env_vars_we_set:
                os.environ.pop(env_var, None)
                self._env_vars_we_set.discard(env_var)
