"""Selector-driven async PDP (product detail page) scrape.

Selectors are supplied per job in ``payload["selectors"]`` (the ``pdp`` sub-object
of a selector profile). All seller/shipping/OOS *filtering* is deferred to the
backend; this module only extracts and normalizes raw fields.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import Any

from .browser import (
    BrowserConfig,
    Metrics,
    TokenBucketRateLimiter,
    attach_net_meter_async,
    close_context_async,
    create_stealth_context_async,
)
from .logging_setup import log_event
from .result import ScrapeResult
from .util import (
    is_network_error,
    normalize_asin,
    parse_price_text,
    pick_amazon_image_url,
    sel_list,
    sel_str,
    valid_asin,
)

LOGGER = logging.getLogger("worker.pdp")

_RETRY_BACKOFF_SECONDS = (1.5, 3.0)
_SCROLL_DELAY_RANGE = (0.25, 0.65)
_JITTER_RANGE = (0.15, 0.55)

_DELIVERY_RELEVANT_RE = re.compile(
    r"delivery|shipping|ship to|ships to|arrives|import charges|^\$[\d,.]+\s*delivery|₪|ils",
    re.IGNORECASE,
)
_EXPLICIT_OOS_RE = re.compile(
    r"currently unavailable|temporarily out of stock|out of stock|unavailable|"
    r"we don't know when or if this item will be back in stock|"
    r"see all buying options",
    re.IGNORECASE,
)

_DEFAULTS = {
    "ready": [
        "#productTitle",
        "#title",
        "h1.a-size-large",
        "#corePriceDisplay_desktop_feature_div",
        "#corePrice_feature_div",
        "#availability",
        "#outOfStock",
        "#add-to-cart-button",
    ],
    "title": ["#productTitle", "#title"],
    "price": [
        "#corePrice_feature_div .a-price .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
        ".reinventPricePriceToPayMargin .a-price .a-offscreen",
        ".apex-pricetopay-value .a-offscreen",
        "#apex-pricetopay-accessibility-label",
        "#tp_price_block_total_price_ww .a-offscreen",
        "span.a-price.a-text-price .a-offscreen",
    ],
    "price_fallback_roots": ["#desktop_buybox", "#buybox", "#offerDisplayFeature_feature_div", "body"],
    "price_whole": ".a-price-whole",
    "price_fraction": ".a-price-fraction",
    "image": ["#landingImage", "#imgBlkFront", "#main-image"],
    "image_dynamic_attr": "data-a-dynamic-image",
    "shipping_roots": ["#qualifiedBuybox", "#desktop_buybox", "#buybox", "#offerDisplayFeature_feature_div"],
    "shipping_secondary_span": "span.a-color-secondary",
    "shipping_nodes": [
        "[id^='mir-layout-DELIVERY_BLOCK-slot-']",
        "#deliveryBlockMessage",
        "#mir-layout-DELIVERY_BLOCK-slot-PRIMARYDELIVERYBLOCKLARGE",
        "#mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE",
        "#ddmDeliveryMessage",
        "[data-cy='delivery-recipe']",
    ],
    "shipping_price_attr": "[data-csa-c-delivery-price]",
    "merchant_feature_names": ["desktop-merchant-info", "desktop-fulfiller-info"],
    "merchant_roots": [
        "#merchantInfoFeature_feature_div",
        "#tabular-buybox",
        "#offerDisplayFeature_feature_div",
        "#desktop_buybox",
        "#buybox",
        "#desktop_accordion",
    ],
    "availability": ["#availability", "#outOfStock", "#desktop_buybox #availability"],
    "oos_container": "#outOfStock",
    "captcha_form": "form[action*='validateCaptcha']",
}


def _skip_row(asin: str, reason: str) -> dict[str, Any]:
    return {"asin": asin, "_skip_update": True, "skip_reason": reason}


def _explicit_oos_from_text(text: str | None) -> bool:
    if not text:
        return False
    return bool(_EXPLICIT_OOS_RE.search(" ".join(str(text).split())))


async def _extract_title(page: Any, sels: list[str]) -> str:
    for sel in sels:
        try:
            node = await page.query_selector(sel)
            if not node:
                continue
            text = (await node.inner_text() or "").strip()
            if text:
                return text
        except Exception:
            continue
    return ""


async def _extract_price(page: Any, selectors: dict[str, Any]) -> float | None:
    for sel in sel_list(selectors, "price", _DEFAULTS["price"]):
        try:
            el = await page.query_selector(sel)
            if not el:
                continue
            raw = (await el.inner_text() or "").strip()
        except Exception:
            raw = ""
        price = parse_price_text(raw)
        if price is not None:
            return price

    whole_sel = sel_str(selectors, "price_whole", _DEFAULTS["price_whole"])
    frac_sel = sel_str(selectors, "price_fraction", _DEFAULTS["price_fraction"])
    for root_sel in sel_list(selectors, "price_fallback_roots", _DEFAULTS["price_fallback_roots"]):
        try:
            root = await page.query_selector(root_sel)
            if not root:
                continue
            whole = await root.query_selector(whole_sel)
            frac = await root.query_selector(frac_sel)
            if whole and frac:
                w = (await whole.inner_text() or "").strip().replace(",", "").replace(".", "")
                f = (await frac.inner_text() or "").strip()
                if w.isdigit() and f.isdigit():
                    return float(f"{w}.{f}")
        except Exception:
            continue
    return None


async def _extract_image(page: Any, selectors: dict[str, Any]) -> str | None:
    dynamic_attr = sel_str(selectors, "image_dynamic_attr", _DEFAULTS["image_dynamic_attr"])
    for sel in sel_list(selectors, "image", _DEFAULTS["image"]):
        try:
            el = await page.query_selector(sel)
            if not el:
                continue
            raw_dynamic = await el.get_attribute(dynamic_attr) or ""
            if raw_dynamic:
                try:
                    candidates = json.loads(raw_dynamic)
                    if isinstance(candidates, dict) and candidates:
                        picked = pick_amazon_image_url(candidates, rank=1)
                        if picked:
                            return picked
                except Exception:
                    pass
            href = await el.get_attribute("src")
            if href and href.startswith("http"):
                return href.strip()
        except Exception:
            continue
    return None


async def _extract_shipping(page: Any, selectors: dict[str, Any]) -> str:
    lines: list[str] = []

    def add_line(value: str | None) -> None:
        if not value:
            return
        for part in re.split(r"[\r\n]+", value):
            line = " ".join(part.split())
            if line and line not in lines:
                lines.append(line)

    secondary_span = sel_str(selectors, "shipping_secondary_span", _DEFAULTS["shipping_secondary_span"])
    for root_sel in sel_list(selectors, "shipping_roots", _DEFAULTS["shipping_roots"]):
        try:
            root = await page.query_selector(root_sel)
            if not root:
                continue
            for el in await root.query_selector_all(secondary_span):
                text = (await el.inner_text() or "").strip()
                if text and _DELIVERY_RELEVANT_RE.search(text):
                    add_line(text)
        except Exception:
            continue

    for sel in sel_list(selectors, "shipping_nodes", _DEFAULTS["shipping_nodes"]):
        try:
            el = await page.query_selector(sel)
            if not el:
                continue
            text = (await el.inner_text() or "").strip()
            if text:
                add_line(text)
        except Exception:
            continue

    price_attr_sel = sel_str(selectors, "shipping_price_attr", _DEFAULTS["shipping_price_attr"])
    try:
        for el in await page.query_selector_all(price_attr_sel):
            attr_name = price_attr_sel.strip("[]").split("=")[0]
            price = (await el.get_attribute(attr_name) or "").strip()
            text = (await el.inner_text() or "").strip()
            add_line(" ".join(x for x in (price, text) if x))
    except Exception:
        pass

    return "\n".join(lines)


async def _extract_merchant_blob(page: Any, selectors: dict[str, Any]) -> str:
    parts: list[str] = []
    feature_names = sel_list(selectors, "merchant_feature_names", _DEFAULTS["merchant_feature_names"])
    for feature_name in feature_names:
        try:
            root = await page.query_selector(
                f'.offer-display-feature-text[offer-display-feature-name="{feature_name}"]'
            )
            if not root:
                continue
            text = (await root.inner_text() or "").strip()
            if text and text not in parts:
                parts.append(text)
        except Exception:
            continue
    for sel in sel_list(selectors, "merchant_roots", _DEFAULTS["merchant_roots"]):
        try:
            node = await page.query_selector(sel)
            if not node:
                continue
            text = (await node.inner_text() or "").strip()
            if text and text not in parts:
                parts.append(text)
        except Exception:
            continue
    return "\n".join(parts)


async def _extract_availability(page: Any, selectors: dict[str, Any]) -> str:
    for sel in sel_list(selectors, "availability", _DEFAULTS["availability"]):
        try:
            el = await page.query_selector(sel)
            if not el:
                continue
            text = (await el.inner_text() or "").strip()
            if text:
                return text
        except Exception:
            continue
    return ""


async def _detect_explicit_oos(page: Any, selectors: dict[str, Any], availability_text: str) -> bool:
    if _explicit_oos_from_text(availability_text):
        return True
    oos_sel = sel_str(selectors, "oos_container", _DEFAULTS["oos_container"])
    try:
        if await page.query_selector(oos_sel):
            return True
    except Exception:
        pass
    return False


def _clamp_concurrency(raw: Any) -> int:
    try:
        return max(1, min(4, int(raw)))
    except (TypeError, ValueError):
        return 2


def _resolve_pdp_quality(result: ScrapeResult) -> str:
    if result.captcha:
        return "captcha"
    if result.error and result.error.startswith("network:"):
        return "network"
    if result.items_ok > 0:
        return "ok"
    reasons = {r.get("skip_reason") for r in result.rows if r.get("_skip_update")}
    if reasons == {"parse_failed"} or "parse_failed" in reasons:
        return "parse_failed"
    if not result.rows:
        return "empty"
    return "empty"


async def _scrape_async(
    asins: list[str],
    selectors: dict[str, Any],
    *,
    browser_config: BrowserConfig,
    max_concurrent: int,
    rate_limiter: TokenBucketRateLimiter | None,
    attempt: int,
) -> ScrapeResult:
    from playwright.async_api import async_playwright

    started = time.monotonic()
    timing_ms: dict[str, int] = {"goto": 0, "ready_wait": 0, "total": 0}
    metrics = Metrics()
    captcha_form = sel_str(selectors, "captcha_form", _DEFAULTS["captcha_form"])
    ready_selector = ", ".join(sel_list(selectors, "ready", _DEFAULTS["ready"]))
    title_sels = sel_list(selectors, "title", _DEFAULTS["title"])

    wait_until = browser_config.wait_until
    goto_timeout = browser_config.goto_timeout_ms
    ready_wait = max(3_000, browser_config.ready_wait_ms)
    max_goto_retries = max(1, browser_config.max_goto_retries)

    sem = asyncio.Semaphore(max_concurrent)
    captcha_abort = asyncio.Event()
    network_error: dict[str, str] = {}

    async with async_playwright() as pw:
        context = await create_stealth_context_async(pw, metrics=metrics, browser_config=browser_config)

        async def worker(idx: int, asin: str) -> tuple[int, dict[str, Any]]:
            if captcha_abort.is_set() or network_error:
                return idx, _skip_row(asin, "run_aborted")
            async with sem:
                if captcha_abort.is_set() or network_error:
                    return idx, _skip_row(asin, "run_aborted")
                await asyncio.sleep(random.uniform(*_JITTER_RANGE))
                if rate_limiter:
                    await asyncio.to_thread(rate_limiter.acquire)
                if captcha_abort.is_set() or network_error:
                    return idx, _skip_row(asin, "run_aborted")

                url = f"https://www.amazon.com/dp/{asin}"
                last_reason = "navigation_failed"
                for goto_attempt in range(1, max_goto_retries + 1):
                    if captcha_abort.is_set() or network_error:
                        return idx, _skip_row(asin, "run_aborted")
                    page = await context.new_page()
                    attach_net_meter_async(page, metrics)
                    try:
                        page.set_default_timeout(2_000)
                        page.set_default_navigation_timeout(goto_timeout)
                        goto_started = time.monotonic()
                        try:
                            await page.goto(url, wait_until=wait_until, timeout=goto_timeout)
                        except Exception as exc:
                            timing_ms["goto"] += int((time.monotonic() - goto_started) * 1000)
                            if is_network_error(exc):
                                network_error["detail"] = str(exc)
                                return idx, _skip_row(asin, "network")
                            last_reason = "navigation_failed"
                            if goto_attempt < max_goto_retries:
                                await asyncio.sleep(random.uniform(*_RETRY_BACKOFF_SECONDS))
                                continue
                            return idx, _skip_row(asin, last_reason)
                        timing_ms["goto"] += int((time.monotonic() - goto_started) * 1000)

                        title_l = (await page.title() or "").lower()
                        cap_el = await page.query_selector(captcha_form)
                        if "robot check" in title_l or cap_el:
                            log_event(LOGGER, logging.WARNING, "pdp captcha detected", asin=asin)
                            captcha_abort.set()
                            return idx, _skip_row(asin, "captcha")

                        ready_ok = False
                        ready_started = time.monotonic()
                        try:
                            await page.wait_for_selector(
                                ready_selector, state="attached", timeout=ready_wait
                            )
                            ready_ok = True
                        except Exception:
                            ready_ok = False
                        timing_ms["ready_wait"] += int((time.monotonic() - ready_started) * 1000)

                        title = await _extract_title(page, title_sels) or (await page.title() or "").strip()
                        merchant_blob = await _extract_merchant_blob(page, selectors)
                        price = await _extract_price(page, selectors)
                        shipping = await _extract_shipping(page, selectors)
                        availability_text = await _extract_availability(page, selectors)
                        explicit_oos = await _detect_explicit_oos(page, selectors, availability_text)
                        image_url = await _extract_image(page, selectors)

                        if not ready_ok and not title and price is None:
                            return idx, _skip_row(asin, "parse_failed")

                        await asyncio.sleep(random.uniform(*_SCROLL_DELAY_RANGE))
                        return idx, {
                            "asin": asin,
                            "title": title,
                            "price": price,
                            "shipping_text": shipping,
                            "availability_text": availability_text,
                            "image_url": image_url,
                            "product_url": url,
                            "merchant_blob": merchant_blob,
                            "explicit_oos": bool(explicit_oos),
                        }
                    except Exception as exc:
                        if is_network_error(exc):
                            network_error["detail"] = str(exc)
                            return idx, _skip_row(asin, "network")
                        log_event(LOGGER, logging.WARNING, "pdp parse failed", asin=asin, error=str(exc))
                        return idx, _skip_row(asin, "parse_failed")
                    finally:
                        await page.close()
                return idx, _skip_row(asin, last_reason)

        tasks = [worker(idx, asin) for idx, asin in enumerate(asins)]
        try:
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await close_context_async(context)

    pairs: list[tuple[int, dict[str, Any]]] = []
    for item in gathered:
        if isinstance(item, tuple) and len(item) == 2:
            pairs.append((item[0], item[1]))
        elif isinstance(item, Exception) and is_network_error(item):
            network_error.setdefault("detail", str(item))
    pairs.sort(key=lambda x: x[0])
    rows = [row for _, row in pairs]

    items_ok = sum(1 for r in rows if not r.get("_skip_update"))
    items_skipped = len(rows) - items_ok
    error = f"network: {network_error['detail']}" if network_error else None
    timing_ms["total"] = int((time.monotonic() - started) * 1000)
    result = ScrapeResult(
        rows=rows,
        metrics=metrics,
        captcha=captcha_abort.is_set(),
        error=error,
        items_ok=items_ok,
        items_skipped=items_skipped,
        browser_profile=browser_config.profile,
        attempt=attempt,
        timing_ms=timing_ms,
    )
    result.scrape_quality = _resolve_pdp_quality(result)
    return result


def scrape_pdp(
    browser_config: BrowserConfig,
    scrape: dict[str, Any],
    selectors: dict[str, Any],
    *,
    rate_limiter: TokenBucketRateLimiter | None,
    attempt: int = 1,
) -> ScrapeResult:
    raw_asins = scrape.get("asins") or []
    pdp_selectors = selectors.get("pdp") if isinstance(selectors.get("pdp"), dict) else selectors
    max_concurrent = _clamp_concurrency(scrape.get("max_concurrent", 2))

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_asins:
        asin = normalize_asin(raw)
        if not valid_asin(asin) or asin in seen:
            continue
        seen.add(asin)
        normalized.append(asin)

    if not normalized:
        empty = ScrapeResult(
            rows=[],
            metrics=Metrics(),
            scrape_quality="empty",
            browser_profile=browser_config.profile,
            attempt=attempt,
        )
        return empty

    log_event(
        LOGGER,
        logging.INFO,
        "pdp scrape starting",
        asins=len(normalized),
        max_concurrent=max_concurrent,
        headless=browser_config.headless,
        profile=browser_config.profile,
        attempt=attempt,
    )
    started = time.monotonic()
    result = asyncio.run(
        _scrape_async(
            normalized,
            pdp_selectors,
            browser_config=browser_config,
            max_concurrent=max_concurrent,
            rate_limiter=rate_limiter,
            attempt=attempt,
        )
    )
    log_event(
        LOGGER,
        logging.INFO,
        "pdp scrape done",
        items_ok=result.items_ok,
        items_skipped=result.items_skipped,
        captcha=result.captcha,
        scrape_quality=result.scrape_quality,
        elapsed_s=round(time.monotonic() - started, 2),
    )
    return result
