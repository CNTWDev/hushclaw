"""Secret storage helpers for credentials that should not live in TOML."""

from hushclaw.secrets.registry import (
    API_KEY_ENV_MAP,
    ApiKeySpec,
    api_key_secret_ref,
    api_key_secret_uri,
    api_key_status_registry,
    get_api_key_spec,
    known_api_key_specs,
    resolve_api_key,
    resolve_config_api_key,
)


def get_secret_store():
    from hushclaw.secrets.store import get_secret_store as _get_secret_store

    return _get_secret_store()


def __getattr__(name: str):
    if name == "FileSecretStore":
        from hushclaw.secrets.store import FileSecretStore

        return FileSecretStore
    raise AttributeError(name)

__all__ = [
    "API_KEY_ENV_MAP",
    "ApiKeySpec",
    "FileSecretStore",
    "api_key_secret_ref",
    "api_key_secret_uri",
    "api_key_status_registry",
    "get_api_key_spec",
    "get_secret_store",
    "known_api_key_specs",
    "resolve_api_key",
    "resolve_config_api_key",
]
