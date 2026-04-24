from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

from hushclaw.util.logging import get_logger
from hushclaw.util.playwright_setup import ensure_playwright, get_playwright_install_hint

log = get_logger("render.share")


class ShareCardRenderer:
    def __init__(
        self,
        base_url: str,
        *,
        headless: bool = True,
        ttl_seconds: int = 600,
        max_entries: int = 64,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headless = bool(headless)
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._max_entries = max(8, int(max_entries))
        self._lock = asyncio.Lock()
        self._pw = None
        self._browser = None
        self._cache: OrderedDict[str, tuple[float, bytes]] = OrderedDict()

    async def start(self) -> None:
        async with self._lock:
            if self._browser is not None:
                return
            if not ensure_playwright():
                raise RuntimeError(get_playwright_install_hint())
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=self._headless)

    async def stop(self) -> None:
        async with self._lock:
            self._cache.clear()
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

    def _cache_get(self, key: str) -> bytes | None:
        if not key:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, data = entry
        if (time.time() - ts) > self._ttl_seconds:
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return data

    def _cache_put(self, key: str, data: bytes) -> None:
        if not key:
            return
        self._cache[key] = (time.time(), data)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    async def render_png(
        self,
        *,
        browser_payload: dict,
        css_width: int,
        css_height: int,
        scale: float,
        cache_key: str = "",
    ) -> bytes:
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        await self.start()
        assert self._browser is not None

        context = await self._browser.new_context(
            viewport={"width": int(css_width), "height": int(css_height)},
            device_scale_factor=max(1.0, float(scale)),
            color_scheme="dark" if browser_payload.get("theme") == "dark" else "light",
        )
        page = await context.new_page()
        try:
            await page.goto(f"{self._base_url}/share-render.html", wait_until="networkidle")
            await page.evaluate(
                "(payload) => window.__HC_SHARE_RENDER__.render(payload)",
                browser_payload,
            )
            card = page.locator(".cimg-card.render-ready")
            await card.wait_for(timeout=5000)
            png = await card.screenshot(type="png", animations="disabled")
            self._cache_put(cache_key, png)
            return png
        finally:
            await context.close()
