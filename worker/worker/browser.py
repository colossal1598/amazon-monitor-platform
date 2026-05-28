"""Browser factory: stealth context, token-bucket rate limiter, heavy-resource
blocking, proxy support, and Israel-locale / USD storefront cookies.

Ported and generalized from the original ``browser_factory`` so the worker can
build both sync (SERP) and async (PDP multi-tab) contexts that look human.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import BrowserContext as AsyncBrowserContext
from playwright.sync_api import BrowserContext, Route, sync_playwright

try:  # playwright-stealth >= 2.0
    from playwright_stealth import Stealth  # type: ignore
except Exception:  # pragma: no cover - older import path
    from playwright_stealth.stealth import Stealth  # type: ignore

_HEAVY_RESOURCE_TYPES = frozenset({"image", "media", "font"})

# Blocking images/fonts can prevent domcontentloaded; commit + downstream selector waits gate readiness.
NAV_WAIT_UNTIL = "commit"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.6668.90 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.69 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.86 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.6834.110 Safari/537.36",
]

STEALTH = Stealth()

_AMAZON_COOKIES: list[dict[str, Any]] = [
    {"name": "i18n-prefs", "value": "USD", "domain": ".amazon.com", "path": "/", "secure": True},
    {"name": "lc-main", "value": "en_US", "domain": ".amazon.com", "path": "/", "secure": True},
]

_CONTEXT_KWARGS: dict[str, Any] = {
    "locale": "en-IL",
    "timezone_id": "Asia/Jerusalem",
    "geolocation": {"latitude": 31.5, "longitude": 34.8},
    "permissions": ["geolocation"],
}


@dataclass
class Metrics:
    """Per-job network and blocking counters."""

    net_bytes: int = 0
    blocked_heavy: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_bytes(self, n: int) -> None:
        with self._lock:
            self.net_bytes += max(0, int(n))

    def bump_blocked(self) -> None:
        with self._lock:
            self.blocked_heavy += 1

    @property
    def net_kb(self) -> float:
        return round(self.net_bytes / 1024.0, 2)


class TokenBucketRateLimiter:
    """Simple request budget so the scraper does not hit Amazon too fast."""

    def __init__(self, capacity: int, refill_per_second: float) -> None:
        self.capacity = max(1, capacity)
        self.tokens = float(self.capacity)
        self.refill_per_second = max(0.0001, refill_per_second)
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
                self.last_refill = now
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
                wait_seconds = (tokens - self.tokens) / self.refill_per_second
            time.sleep(max(0.05, wait_seconds))


def make_rate_limiter(max_requests_per_minute: int) -> TokenBucketRateLimiter:
    return TokenBucketRateLimiter(
        capacity=max_requests_per_minute,
        refill_per_second=max_requests_per_minute / 60.0,
    )


def _content_length(response: Any) -> int:
    try:
        raw = response.headers.get("content-length")
        return int(raw) if raw else 0
    except Exception:
        return 0


def attach_net_meter_sync(page: Any, metrics: Metrics) -> None:
    page.on("response", lambda resp: metrics.add_bytes(_content_length(resp)))


def attach_net_meter_async(page: Any, metrics: Metrics) -> None:
    page.on("response", lambda resp: metrics.add_bytes(_content_length(resp)))


def _should_abort_heavy(route: Route) -> bool:
    return route.request.resource_type in _HEAVY_RESOURCE_TYPES


def register_heavy_resource_blocking_sync(context: BrowserContext, metrics: Metrics) -> None:
    def handler(route: Route) -> None:
        if _should_abort_heavy(route):
            metrics.bump_blocked()
            route.abort()
        else:
            route.continue_()

    context.route("**/*", handler)


async def register_heavy_resource_blocking_async(context: AsyncBrowserContext, metrics: Metrics) -> None:
    async def handler(route: Route) -> None:
        if _should_abort_heavy(route):
            metrics.bump_blocked()
            await route.abort()
        else:
            await route.continue_()

    await context.route("**/*", handler)


def _random_viewport() -> dict[str, int]:
    return {"width": random.randint(1870, 1970), "height": random.randint(1030, 1130)}


def create_stealth_context(
    *,
    metrics: Metrics,
    headless: bool = True,
    proxy_url: Optional[str] = None,
    persistent_dir: Optional[str] = None,
) -> BrowserContext:
    """Start a sync Playwright stealth context (used by the SERP scraper)."""
    p = sync_playwright().start()
    chromium = p.chromium
    launch_args: dict[str, Any] = {"channel": "chrome", "headless": headless}
    if proxy_url:
        launch_args["proxy"] = {"server": proxy_url}

    context_kwargs = {
        "user_agent": random.choice(USER_AGENTS),
        "viewport": _random_viewport(),
        **_CONTEXT_KWARGS,
    }

    if persistent_dir:
        Path(persistent_dir).mkdir(parents=True, exist_ok=True)
        context = chromium.launch_persistent_context(persistent_dir, **launch_args, **context_kwargs)
    else:
        browser = chromium.launch(**launch_args)
        context = browser.new_context(**context_kwargs)

    context.set_extra_http_headers({"Accept-Language": "en-IL,en;q=0.9"})
    context.add_cookies(_AMAZON_COOKIES)
    context.on("page", lambda page: STEALTH.apply_stealth_sync(page))
    for page in context.pages:
        STEALTH.apply_stealth_sync(page)

    register_heavy_resource_blocking_sync(context, metrics)
    setattr(context, "_pw_runner", p)
    return context


def close_context(context: BrowserContext) -> None:
    pw_runner = getattr(context, "_pw_runner", None)
    try:
        context.close()
    finally:
        if pw_runner is not None:
            pw_runner.stop()


async def create_stealth_context_async(
    pw: Any,
    *,
    metrics: Metrics,
    headless: bool = True,
    proxy_url: Optional[str] = None,
) -> AsyncBrowserContext:
    """Build an async stealth context (used by the multi-tab PDP scraper).

    ``pw`` is an already-started ``async_playwright`` instance. Returns a context
    whose owning browser is stashed on ``context._pw_browser`` for later close.
    """
    launch_kwargs: dict[str, Any] = {"channel": "chrome", "headless": headless}
    if proxy_url:
        launch_kwargs["proxy"] = {"server": proxy_url}

    browser = await pw.chromium.launch(**launch_kwargs)
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport=_random_viewport(),
        **_CONTEXT_KWARGS,
    )
    await register_heavy_resource_blocking_async(context, metrics)
    await context.set_extra_http_headers({"Accept-Language": "en-IL,en;q=0.9"})
    await context.add_cookies(_AMAZON_COOKIES)

    apply_async = getattr(STEALTH, "apply_stealth_async", None)
    if callable(apply_async):
        try:
            await apply_async(context)
        except Exception:
            pass

    setattr(context, "_pw_browser", browser)
    return context


async def close_context_async(context: AsyncBrowserContext) -> None:
    browser = getattr(context, "_pw_browser", None)
    try:
        await context.close()
    finally:
        if browser is not None:
            await browser.close()
