"""Registry and resolution helpers for tool / skill API keys."""
from __future__ import annotations

from dataclasses import dataclass, field
import os


SECRET_URI_PREFIX = "secret://"


@dataclass(frozen=True)
class ApiKeySpec:
    key: str
    label: str
    description: str = ""
    env_vars: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    docs_url: str = ""
    manage_url: str = ""
    category: str = "tool"
    supports_apply_link: bool = True


_KNOWN_API_KEY_SPECS: tuple[ApiKeySpec, ...] = (
    ApiKeySpec(
        key="jina",
        label="Jina Search API Key",
        description="Required by web_search for public web discovery through Jina Search.",
        env_vars=("JINA_API_KEY",),
        aliases=("jina_api_key", "JINA_API_KEY"),
        docs_url="https://jina.ai/",
        manage_url="https://jina.ai/api-dashboard/key-manager",
    ),
    ApiKeySpec(
        key="scrape_creators",
        label="Scrape Creators API Key",
        description="Used by creator scraping skills and related external integrations.",
        env_vars=("SCRAPE_CREATORS_API_KEY",),
        manage_url="https://scrapecreators.com/",
    ),
    ApiKeySpec(
        key="tiktok_client_key",
        label="TikTok Client Key",
        description="Client key for TikTok-related skills and integrations.",
        env_vars=("TIKTOK_CLIENT_KEY",),
        manage_url="https://developers.tiktok.com/apps",
    ),
    ApiKeySpec(
        key="tiktok_client_secret",
        label="TikTok Client Secret",
        description="Client secret paired with the TikTok Client Key.",
        env_vars=("TIKTOK_CLIENT_SECRET",),
        manage_url="https://developers.tiktok.com/apps",
    ),
)

_SPEC_BY_KEY = {item.key: item for item in _KNOWN_API_KEY_SPECS}

# Canonical mapping kept here so loader / tools / UI all share the same source.
API_KEY_ENV_MAP: dict[str, str] = {
    spec.key: spec.env_vars[0]
    for spec in _KNOWN_API_KEY_SPECS
    if spec.env_vars
}


def api_key_secret_ref(key: str) -> str:
    return f"api_keys.{str(key).strip()}"


def api_key_secret_uri(key: str) -> str:
    return f"{SECRET_URI_PREFIX}{api_key_secret_ref(key)}"


def is_secret_uri(value: str) -> bool:
    return isinstance(value, str) and value.startswith(SECRET_URI_PREFIX)


def secret_ref_from_uri(value: str) -> str:
    return str(value)[len(SECRET_URI_PREFIX):] if is_secret_uri(value) else str(value)


def known_api_key_specs() -> list[ApiKeySpec]:
    return list(_KNOWN_API_KEY_SPECS)


def get_api_key_spec(key: str) -> ApiKeySpec | None:
    return _SPEC_BY_KEY.get(str(key).strip())


def _coerce_api_key_spec(item) -> ApiKeySpec | None:
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
        category=str(item.get("category") or "tool").strip() or "tool",
        supports_apply_link=bool(item.get("supports_apply_link", True)),
    )


def _candidate_config_keys(key: str) -> list[str]:
    spec = get_api_key_spec(key)
    out = [str(key).strip()]
    if spec:
        for alias in spec.aliases:
            alias = str(alias).strip()
            if alias and alias not in out:
                out.append(alias)
    return out


def _candidate_env_vars(key: str) -> list[str]:
    spec = get_api_key_spec(key)
    out = list(spec.env_vars) if spec else []
    fallback = str(key).strip().upper()
    if fallback and fallback not in out:
        out.append(fallback)
    return out


def resolve_config_api_key(api_keys: dict | None, secret_store, key: str) -> tuple[str, str]:
    """Resolve a key from config and secret store only.

    Returns ``(value, source)`` where source is one of:
    ``secret_store``, ``config``, or ``unset``.
    """
    if not isinstance(api_keys, dict):
        return "", "unset"
    for candidate in _candidate_config_keys(key):
        raw = api_keys.get(candidate)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        if is_secret_uri(text):
            ref = secret_ref_from_uri(text)
            value = secret_store.get(ref, "") if secret_store is not None else ""
            value = str(value).strip()
            if value:
                return value, "secret_store"
            continue
        return text, "config"
    return "", "unset"


def resolve_api_key(api_keys: dict | None, secret_store, key: str) -> tuple[str, str]:
    """Resolve a key from env first, then secret store / config."""
    for env_var in _candidate_env_vars(key):
        value = (os.environ.get(env_var) or "").strip()
        if value:
            return value, "env"
    return resolve_config_api_key(api_keys, secret_store, key)


def api_key_status_registry(api_keys: dict | None, secret_store, extra_specs: list | None = None) -> list[dict]:
    """Return frontend-friendly metadata + status for known and custom API keys."""
    seen: set[str] = set()
    items: list[dict] = []
    specs: list[ApiKeySpec] = list(_KNOWN_API_KEY_SPECS)
    for raw in extra_specs or []:
        spec = _coerce_api_key_spec(raw)
        if spec is None:
            continue
        existing_idx = next((i for i, item in enumerate(specs) if item.key == spec.key), None)
        if existing_idx is None:
            specs.append(spec)
        else:
            specs[existing_idx] = spec
    for spec in specs:
        value, source = resolve_config_api_key(api_keys, secret_store, spec.key)
        if not value:
            value, source = resolve_api_key({}, secret_store, spec.key)
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
            value, source = resolve_config_api_key(api_keys, secret_store, key)
            if not value:
                value, source = resolve_api_key({}, secret_store, key)
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
