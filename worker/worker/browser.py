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
from typing import TYPE_CHECKING, Any, Optional

from playwright.sync_api import BrowserContext, Route, sync_playwright

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext as AsyncBrowserContext

try:  # playwright-stealth >= 2.0
    from playwright_stealth import Stealth  # type: ignore
except Exception:  # pragma: no cover - older import path
    from playwright_stealth.stealth import Stealth  # type: ignore

_HEAVY_RESOURCE_TYPES = frozenset({"image", "media", "font"})

# Blocking images/fonts can prevent domcontentloaded; commit + downstream selector waits gate readiness.
NAV_WAIT_UNTIL = "commit"

_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "fast": {
        "goto_timeout_ms": 12_000,
        "ready_wait_ms": 8_000,
        "max_goto_retries": 1,
        "wait_until": NAV_WAIT_UNTIL,
    },
    "retry": {
        "goto_timeout_ms": 20_000,
        "ready_wait_ms": 15_000,
        "max_goto_retries": 2,
        "wait_until": NAV_WAIT_UNTIL,
    },
}

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

_pw_instance: Any = None
_pw_lock = threading.Lock()


def _get_playwright() -> Any:
    """Return a module-level sync Playwright instance, starting it on first use."""
    global _pw_instance
    with _pw_lock:
        if _pw_instance is None:
            _pw_instance = sync_playwright().start()
        return _pw_instance


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
class BrowserConfig:
    """Per-job browser / navigation settings from the job envelope."""

    profile: str = "fast"
    block_heavy: bool = True
    headless: bool = True
    channel: str = "chrome"
    proxy_url: Optional[str] = None
    goto_timeout_ms: int = 12_000
    ready_wait_ms: int = 8_000
    max_goto_retries: int = 1
    wait_until: str = NAV_WAIT_UNTIL
    rate_limit_rpm: Optional[int] = None


def parse_browser_config(
    payload: dict[str, Any],
    *,
    env_headless: bool = True,
    env_proxy_url: Optional[str] = None,
    profile_override: Optional[str] = None,
) -> BrowserConfig:
    """Build ``BrowserConfig`` from the new ``browser`` envelope or legacy top-level fields."""
    browser_raw = payload.get("browser")
    raw: dict[str, Any] = dict(browser_raw) if isinstance(browser_raw, dict) else {}

    profile = profile_override or raw.get("profile") or "fast"
    if profile not in _PROFILE_DEFAULTS:
        profile = "fast"
    defaults = _PROFILE_DEFAULTS[profile]

    nav = payload.get("nav") or {}
    if isinstance(payload.get("selectors"), dict):
        nav = payload["selectors"].get("nav") or nav
    if not isinstance(nav, dict):
        nav = {}

    def _int(key: str, nav_keys: tuple[str, ...], default: int) -> int:
        for source in (raw, nav):
            if not isinstance(source, dict):
                continue
            for k in (key, *nav_keys):
                if k in source and source[k] is not None:
                    try:
                        return int(source[k])
                    except (TypeError, ValueError):
                        pass
        return default

    headless = raw.get("headless")
    if headless is None:
        headless = payload.get("headless")
    if headless is None:
        headless = env_headless

    proxy_url = raw.get("proxy_url")
    if proxy_url is None or (isinstance(proxy_url, str) and not proxy_url.strip()):
        proxy_url = env_proxy_url

    rate_limit_rpm = raw.get("rate_limit_rpm")
    if rate_limit_rpm is not None:
        try:
            rate_limit_rpm = int(rate_limit_rpm)
        except (TypeError, ValueError):
            rate_limit_rpm = None

    return BrowserConfig(
        profile=profile,
        block_heavy=bool(raw.get("block_heavy", True)),
        headless=bool(headless),
        channel=str(raw.get("channel") or "chrome"),
        proxy_url=proxy_url,
        goto_timeout_ms=_int("goto_timeout_ms", ("pdp_goto_timeout_ms", "goto_timeout_ms"), defaults["goto_timeout_ms"]),
        ready_wait_ms=_int(
            "ready_wait_ms",
            ("pdp_ready_wait_ms", "serp_card_wait_ms"),
            defaults["ready_wait_ms"],
        ),
        max_goto_retries=_int("max_goto_retries", (), defaults["max_goto_retries"]),
        wait_until=str(raw.get("wait_until") or nav.get("wait_until") or defaults["wait_until"]),
        rate_limit_rpm=rate_limit_rpm,
    )


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
    browser_config: BrowserConfig,
    persistent_dir: Optional[str] = None,
) -> BrowserContext:
    """Start a sync Playwright stealth context (used by the SERP scraper).

    Uses a single shared Playwright instance for the lifetime of the worker
    process — calling ``sync_playwright().start()`` more than once triggers
    an "asyncio loop" error in Playwright >= 1.44.
    """
    pw = _get_playwright()
    chromium = pw.chromium
    launch_args: dict[str, Any] = {
        "channel": browser_config.channel,
        "headless": browser_config.headless,
    }
    if browser_config.proxy_url:
        launch_args["proxy"] = {"server": browser_config.proxy_url}

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

    if browser_config.block_heavy:
        register_heavy_resource_blocking_sync(context, metrics)
    return context


def close_context(context: BrowserContext) -> None:
    try:
        context.close()
    except Exception:
        pass


async def create_stealth_context_async(
    pw: Any,
    *,
    metrics: Metrics,
    browser_config: BrowserConfig,
) -> AsyncBrowserContext:
    """Build an async stealth context (used by the multi-tab PDP scraper).

    ``pw`` is an already-started ``async_playwright`` instance. Returns a context
    whose owning browser is stashed on ``context._pw_browser`` for later close.
    """
    launch_kwargs: dict[str, Any] = {
        "channel": browser_config.channel,
        "headless": browser_config.headless,
    }
    if browser_config.proxy_url:
        launch_kwargs["proxy"] = {"server": browser_config.proxy_url}

    browser = await pw.chromium.launch(**launch_kwargs)
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport=_random_viewport(),
        **_CONTEXT_KWARGS,
    )
    if browser_config.block_heavy:
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
