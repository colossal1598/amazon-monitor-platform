"""Selector-driven sync SERP (search results page) scrape.

Selectors are supplied per job in ``payload["selectors"]`` (the ``serp`` sub-object
of a selector profile). The worker extracts raw card fields only; stock and seller
filtering happen in the backend.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import unicodedata
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .browser import (
    BrowserConfig,
    Metrics,
    TokenBucketRateLimiter,
    attach_net_meter_sync,
    close_context,
    create_stealth_context,
)
from .logging_setup import log_event
from .result import ScrapeResult
from .util import card_list_price, is_network_error, pick_amazon_image_url, valid_asin

LOGGER = logging.getLogger("worker.serp")

_SCROLL_DELAY_RANGE = (0.25, 0.65)
_PAGINATION_DELAY_RANGE = (2.0, 4.5)

_DEFAULTS = {
    "result_card": "div[data-component-type='s-search-result']",
    "title": [
        "[data-cy='title-recipe']",
        "h2",
        "a.a-link-normal.s-line-clamp-2",
        "h2 a span",
        "h2 span",
    ],
    "price_recipe": '[data-cy="price-recipe"]',
    "price": [
        "span.a-price span.a-offscreen",
        "span[data-a-color='base'] span.a-offscreen",
    ],
    "seller_regions": [
        "div[data-cy='offer-recipe']",
        "div[data-cy='secondary-offer-recipe']",
        "div[data-cy='delivery-recipe']",
        "div[data-cy='seller-recipe']",
        ".puis-min-offer-desktop-container",
        ".puisg-col-inner .a-section.a-spacing-none.a-spacing-top-micro",
        "div.s-delivery-recipe",
    ],
    "seller_has_text": [
        "span:has-text('Sold by')",
        "span:has-text('Ships from')",
        "div.a-row:has-text('Sold by')",
        "div.a-row:has-text('Ships from')",
        "[data-seller]",
    ],
    "delivery_block": 'div[data-cy="delivery-block"]',
    "delivery_primary": ".udm-primary-delivery-message",
    "shipping_legacy": [
        "span:has-text('FREE Shipping')",
        "span:has-text('FREE delivery')",
        "span:has-text('to Israel')",
        "span.a-color-secondary",
    ],
    "availability": [
        "span.a-size-base.a-color-price",
        "span[class*='availability']",
        "div[class*='availability'] span",
    ],
    "image": "img.s-image",
    "image_dynamic_attr": "data-a-dynamic-image",
    "product_link": "h2 a",
    "fallback_roots": [
        "div.s-main-slot div.s-result-item.s-asin[data-asin]",
        "div.s-main-slot div[role='listitem'][data-asin]",
    ],
    "carousel_roots": [
        ".s-searchgrid-carousel div[data-asin]",
        "[cel_widget_id*='FEATURED_ASINS_LIST'] div[data-asin].s-result-item",
    ],
    "more_results_heading": 'h2:has-text("More results")',
    "pagination_next": "a.s-pagination-next",
    "captcha_form": "form[action*='validateCaptcha']",
}


def _sel_list(selectors: dict[str, Any], key: str) -> list[str]:
    from .util import sel_list

    return sel_list(selectors, key, _DEFAULTS[key])


def _sel_str(selectors: dict[str, Any], key: str) -> str:
    from .util import sel_str

    return sel_str(selectors, key, _DEFAULTS[key])


def _normalize_ascii(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", (value or "").lower().strip())
    return decomposed.encode("ascii", "ignore").decode("ascii")


def _looks_like_seller_blob(value: str) -> bool:
    text = _normalize_ascii(value)
    if not text or "out of 5 stars" in text:
        return False
    return (
        "sold by" in text
        or "ships from" in text
        or "amazon export llc" in text
        or re.search(r"\bamazon\.com\b", text) is not None
    )


def _extract_by_selectors(card: Any, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            node = card.query_selector(selector)
        except Exception:
            continue
        if not node:
            continue
        text = (node.inner_text() or "").strip()
        if text:
            return text
    return ""


def _extract_title(card: Any, sels: dict[str, Any]) -> str:
    text = _extract_by_selectors(card, _sel_list(sels, "title"))
    return " ".join(text.split()) if text else ""


def _extract_price(card: Any, sels: dict[str, Any]) -> float | None:
    recipe_sel = _sel_str(sels, "price_recipe")
    price_recipe = card.query_selector(recipe_sel)
    if price_recipe:
        for off in price_recipe.query_selector_all("span.a-price span.a-offscreen"):
            value = card_list_price((off.inner_text() or "").strip())
            if value is not None:
                return value
        value = card_list_price((price_recipe.inner_text() or "").strip())
        if value is not None:
            return value
    for selector in _sel_list(sels, "price"):
        node = card.query_selector(selector)
        if not node:
            continue
        value = card_list_price((node.inner_text() or "").strip())
        if value is not None:
            return value
    return None


def _extract_price_text(card: Any, sels: dict[str, Any]) -> str:
    recipe_sel = _sel_str(sels, "price_recipe")
    price_recipe = card.query_selector(recipe_sel)
    if price_recipe:
        for off in price_recipe.query_selector_all("span.a-price span.a-offscreen"):
            text = (off.inner_text() or "").strip()
            if text:
                return text
    return _extract_by_selectors(card, _sel_list(sels, "price"))


def _extract_availability_text(card: Any, sels: dict[str, Any], card_text: str) -> str:
    text = _extract_by_selectors(card, _sel_list(sels, "availability"))
    return text or card_text


def _extract_seller_text(card: Any, sels: dict[str, Any]) -> str:
    for selector in _sel_list(sels, "seller_regions"):
        node = card.query_selector(selector)
        if not node:
            continue
        text = (node.inner_text() or "").strip()
        if _looks_like_seller_blob(text):
            return text
    for selector in _sel_list(sels, "seller_has_text"):
        try:
            node = card.query_selector(selector)
        except Exception:
            continue
        if not node:
            continue
        text = (node.inner_text() or "").strip()
        if _looks_like_seller_blob(text):
            return text
    card_text = (card.inner_text() or "").strip()
    if _looks_like_seller_blob(card_text):
        m = re.search(r"(sold by[^\n]{0,120}|ships from[^\n]{0,120})", card_text, flags=re.IGNORECASE)
        return m.group(1).strip() if m else card_text
    return ""


def _extract_shipping_text(card: Any, sels: dict[str, Any]) -> str:
    block_sel = _sel_str(sels, "delivery_block")
    block = card.query_selector(block_sel)
    if block:
        primary = block.query_selector(_sel_str(sels, "delivery_primary"))
        if primary:
            text = (primary.inner_text() or "").strip()
            if text:
                return text
        text = (block.inner_text() or "").strip()
        if text:
            return text
    return _extract_by_selectors(card, _sel_list(sels, "shipping_legacy"))


def _extract_image_url(card: Any, sels: dict[str, Any]) -> str | None:
    image_sel = _sel_str(sels, "image")
    dynamic_attr = _sel_str(sels, "image_dynamic_attr")
    image_el = card.query_selector(image_sel)
    if not image_el:
        return None
    raw_dynamic = image_el.get_attribute(dynamic_attr) or ""
    if raw_dynamic:
        try:
            candidates = json.loads(raw_dynamic)
            if isinstance(candidates, dict) and candidates:
                return pick_amazon_image_url(candidates, rank=1)
        except Exception:
            pass
    srcset = image_el.get_attribute("srcset") or ""
    if srcset:
        urls = [p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()]
        if urls:
            return pick_amazon_image_url(urls, rank=1)
    src = image_el.get_attribute("src")
    return src.strip() if src else None


def _extract_product_url(card: Any, sels: dict[str, Any]) -> str | None:
    link = card.query_selector(_sel_str(sels, "product_link"))
    if not link:
        return None
    href = (link.get_attribute("href") or "").strip()
    if not href:
        return None
    return urljoin("https://www.amazon.com", href)


def _collect_row(card: Any, sels: dict[str, Any]) -> dict[str, Any] | None:
    asin = (card.get_attribute("data-asin") or "").strip().upper()
    if not valid_asin(asin):
        return None
    card_text = card.inner_text() or ""
    return {
        "asin": asin,
        "title": _extract_title(card, sels),
        "price": _extract_price(card, sels),
        "price_text": _extract_price_text(card, sels),
        "image_url": _extract_image_url(card, sels),
        "product_url": _extract_product_url(card, sels),
        "seller_text": _extract_seller_text(card, sels),
        "shipping_text": _extract_shipping_text(card, sels),
        "availability_text": _extract_availability_text(card, sels, card_text),
    }


def _set_page_param(search_url: str, page_num: int) -> str:
    parsed = urlparse(search_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["page"] = [str(page_num)]
    new_query = urlencode(query, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def _scroll_to_settle(page: Any, max_steps: int = 10) -> None:
    prev_h = -1
    stable_rounds = 0
    for _ in range(max_steps):
        try:
            h = page.evaluate(
                "() => Math.max(document.documentElement.scrollHeight, document.body && document.body.scrollHeight || 0)"
            )
            h_int = int(h) if h is not None else 0
            vh = page.evaluate("() => window.innerHeight") or 800
            vh_int = max(400, int(vh))
        except Exception:
            break
        if h_int > 0 and abs(h_int - prev_h) < 40:
            stable_rounds += 1
            if stable_rounds >= 2:
                break
        else:
            stable_rounds = 0
        prev_h = h_int
        page.mouse.wheel(0, random.randint(int(vh_int * 0.55), int(vh_int * 0.95)))
        time.sleep(random.uniform(*_SCROLL_DELAY_RANGE))


def _scroll_more_results(page: Any, heading_sel: str) -> None:
    try:
        page.locator(heading_sel).first.scroll_into_view_if_needed(timeout=5000)
        time.sleep(random.uniform(*_SCROLL_DELAY_RANGE))
    except Exception:
        pass


def _is_captcha(page: Any, captcha_form: str) -> bool:
    for _ in range(3):
        try:
            if "robot check" in (page.title() or "").lower():
                return True
            return bool(page.query_selector(captcha_form))
        except PlaywrightError as exc:
            msg = str(exc)
            if "Execution context was destroyed" in msg or "Target closed" in msg or "Target page" in msg:
                time.sleep(0.35)
                continue
            raise
    return False


def _extra_roots(page: Any, selector_groups: list[str], seen_ids: set[int]) -> list[Any]:
    roots: list[Any] = []
    for sel in selector_groups:
        try:
            nodes = page.query_selector_all(sel)
        except Exception:
            nodes = []
        for node in nodes:
            try:
                key = id(node)
            except Exception:
                continue
            if key in seen_ids:
                continue
            asin = (node.get_attribute("data-asin") or "").strip()
            if not valid_asin(asin):
                continue
            seen_ids.add(key)
            roots.append(node)
    return roots


def _resolve_serp_quality(
    *,
    captcha: bool,
    error: str | None,
    rows: list[dict[str, Any]],
    parse_failed: bool,
) -> str:
    if captcha:
        return "captcha"
    if error and error.startswith("network:"):
        return "network"
    if rows:
        return "ok"
    if parse_failed:
        return "parse_failed"
    return "empty"


def scrape_serp(
    browser_config: BrowserConfig,
    scrape: dict[str, Any],
    selectors: dict[str, Any],
    *,
    rate_limiter: TokenBucketRateLimiter | None,
    attempt: int = 1,
) -> ScrapeResult:
    search_url = scrape.get("search_url") or ""
    serp_selectors = selectors.get("serp") if isinstance(selectors.get("serp"), dict) else selectors
    scrape_mode = scrape.get("scrape_mode", "featured_full")
    max_pages = max(1, int(scrape.get("max_pages", 1)))

    if not search_url:
        return ScrapeResult(
            rows=[],
            metrics=Metrics(),
            error="missing search_url",
            scrape_quality="empty",
            browser_profile=browser_config.profile,
            attempt=attempt,
        )

    wait_until = browser_config.wait_until
    goto_timeout = browser_config.goto_timeout_ms
    card_wait = max(3_000, browser_config.ready_wait_ms)
    max_goto_retries = max(1, browser_config.max_goto_retries)

    result_card_sel = _sel_str(serp_selectors, "result_card")
    captcha_form = _sel_str(serp_selectors, "captcha_form")
    pagination_next = _sel_str(serp_selectors, "pagination_next")
    more_results = _sel_str(serp_selectors, "more_results_heading")
    fallback_roots = _sel_list(serp_selectors, "fallback_roots")
    carousel_roots = _sel_list(serp_selectors, "carousel_roots")

    started = time.monotonic()
    timing_ms: dict[str, int] = {"goto": 0, "ready_wait": 0, "total": 0}
    metrics = Metrics()
    rows: list[dict[str, Any]] = []
    items_skipped = 0
    captcha = False
    error: str | None = None
    parse_failed = False

    total_pages = 1 if scrape_mode == "newest_front" else max_pages

    log_event(
        LOGGER,
        logging.INFO,
        "serp scrape starting",
        scrape_mode=scrape_mode,
        max_pages=total_pages,
        headless=browser_config.headless,
        profile=browser_config.profile,
        attempt=attempt,
    )

    context = create_stealth_context(metrics=metrics, browser_config=browser_config)
    try:
        page = context.new_page()
        attach_net_meter_sync(page, metrics)
        page_num = 1
        while page_num <= total_pages:
            if rate_limiter:
                rate_limiter.acquire()
            current_url = _set_page_param(search_url, page_num) if page_num > 1 else search_url
            navigated = False
            for goto_attempt in range(1, max_goto_retries + 1):
                goto_started = time.monotonic()
                try:
                    page.goto(current_url, wait_until=wait_until, timeout=goto_timeout)
                    navigated = True
                    timing_ms["goto"] += int((time.monotonic() - goto_started) * 1000)
                    break
                except Exception as exc:
                    timing_ms["goto"] += int((time.monotonic() - goto_started) * 1000)
                    if is_network_error(exc):
                        error = f"network: {exc}"
                        break
                    if goto_attempt >= max_goto_retries:
                        log_event(
                            LOGGER,
                            logging.WARNING,
                            "serp navigation failed",
                            page=page_num,
                            error=str(exc),
                        )
                        break
                    time.sleep(random.uniform(1.0, 2.0))
            if error or not navigated:
                break

            ready_started = time.monotonic()
            try:
                page.wait_for_selector("body", state="attached", timeout=card_wait)
                time.sleep(0.25)
            except Exception:
                pass

            if _is_captcha(page, captcha_form):
                log_event(LOGGER, logging.WARNING, "serp captcha detected", page=page_num)
                captcha = True
                timing_ms["ready_wait"] += int((time.monotonic() - ready_started) * 1000)
                break

            try:
                page.wait_for_selector(result_card_sel, timeout=card_wait)
            except PlaywrightTimeoutError:
                log_event(LOGGER, logging.WARNING, "serp result cards not found", page=page_num)
                parse_failed = True
                timing_ms["ready_wait"] += int((time.monotonic() - ready_started) * 1000)
                break
            timing_ms["ready_wait"] += int((time.monotonic() - ready_started) * 1000)

            _scroll_to_settle(page)
            if scrape_mode != "newest_front":
                _scroll_more_results(page, more_results)
                _scroll_to_settle(page, max_steps=6)

            seen_asins: set[str] = set()
            seen_ids: set[int] = set()
            try:
                cards = page.query_selector_all(result_card_sel)
            except Exception:
                cards = []
            for card in cards:
                row = _collect_row(card, serp_selectors)
                if row is None:
                    items_skipped += 1
                    continue
                if row["asin"] in seen_asins:
                    continue
                seen_asins.add(row["asin"])
                rows.append(row)

            for card in _extra_roots(page, fallback_roots, seen_ids):
                row = _collect_row(card, serp_selectors)
                if row is None or row["asin"] in seen_asins:
                    continue
                seen_asins.add(row["asin"])
                rows.append(row)

            for card in _extra_roots(page, carousel_roots, seen_ids):
                row = _collect_row(card, serp_selectors)
                if row is None or row["asin"] in seen_asins:
                    continue
                seen_asins.add(row["asin"])
                rows.append(row)

            if scrape_mode == "newest_front" or page_num >= total_pages:
                break
            next_btn = page.query_selector(pagination_next)
            disabled = (next_btn.get_attribute("aria-disabled") if next_btn else "true") == "true"
            if not next_btn or disabled:
                break
            page_num += 1
            time.sleep(random.uniform(*_PAGINATION_DELAY_RANGE))
    except Exception as exc:
        if is_network_error(exc):
            error = f"network: {exc}"
        else:
            error = f"serp: {exc}"
        log_event(LOGGER, logging.WARNING, "serp scrape error", error=str(exc))
    finally:
        close_context(context)

    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        deduped[row["asin"]] = row
    final_rows = list(deduped.values())

    timing_ms["total"] = int((time.monotonic() - started) * 1000)
    scrape_quality = _resolve_serp_quality(
        captcha=captcha,
        error=error,
        rows=final_rows,
        parse_failed=parse_failed,
    )
    result = ScrapeResult(
        rows=final_rows,
        metrics=metrics,
        captcha=captcha,
        error=error,
        items_ok=len(final_rows),
        items_skipped=items_skipped,
        scrape_quality=scrape_quality,
        browser_profile=browser_config.profile,
        attempt=attempt,
        timing_ms=timing_ms,
    )
    log_event(
        LOGGER,
        logging.INFO,
        "serp scrape done",
        items_ok=result.items_ok,
        items_skipped=result.items_skipped,
        captcha=captcha,
        scrape_quality=scrape_quality,
    )
    return result
