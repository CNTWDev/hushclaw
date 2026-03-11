"""Web tools: fetch URLs using urllib (no external deps)."""
from __future__ import annotations

import urllib.error
import urllib.request

from ghostclaw.tools.base import tool, ToolResult


@tool(
    name="fetch_url",
    description="Fetch the content of a URL and return the response body as text.",
)
def fetch_url(url: str, max_bytes: int = 32768, timeout: int = 15) -> ToolResult:
    """Fetch a URL and return the text content."""
    if not url.startswith(("http://", "https://")):
        return ToolResult.error(f"Invalid URL (must start with http/https): {url}")
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GhostClaw/0.1 (+https://github.com/ghostclaw)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes)
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            if len(raw) >= max_bytes:
                text = f"[Content truncated at {max_bytes} bytes]\n{text}"
            return ToolResult.ok(text)
    except urllib.error.HTTPError as e:
        return ToolResult.error(f"HTTP {e.code} error fetching {url}: {e.reason}")
    except urllib.error.URLError as e:
        return ToolResult.error(f"URL error fetching {url}: {e.reason}")
    except Exception as e:
        return ToolResult.error(f"Failed to fetch {url}: {e}")
