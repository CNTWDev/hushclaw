"""Provider adapters for the search runtime."""

from __future__ import annotations


class JinaSearchProvider:
    name = "jina_search"

    def search(
        self,
        query: str,
        *,
        limit: int,
        timeout: int,
        locale: str = "",
        freshness: str = "",
        _config=None,
        _credential_service=None,
    ) -> tuple[dict[str, object], bool]:
        from hushclaw.tools.builtins.web_tools import _web_search_payload

        return _web_search_payload(
            query=query,
            limit=limit,
            timeout=timeout,
            locale=locale,
            freshness=freshness,
            _config=_config,
            _credential_service=_credential_service,
        )


class JinaReaderProvider:
    name = "jina_reader"

    def read(
        self,
        url: str,
        *,
        timeout: int,
        _config=None,
        _credential_service=None,
    ) -> tuple[str, bool]:
        from hushclaw.tools.builtins.web_tools import _jina_read_content

        return _jina_read_content(
            url=url,
            timeout=timeout,
            _config=_config,
            _credential_service=_credential_service,
        )


class LocalFetchProvider:
    name = "fetch_url"

    def fetch(
        self,
        url: str,
        *,
        timeout: int,
    ) -> tuple[str, bool]:
        from hushclaw.tools.builtins.web_tools import _fetch_url_content

        return _fetch_url_content(url=url, timeout=timeout)
