"""Generalized, config-driven filtering and stock qualification.

Ported and de-Pokemon-ified from the original filter_pipeline.py / pdp_scraper.py.
All rules now come from a group's filter config instead of hardcoded heuristics.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_NOT_SHIPPABLE_PATTERNS = (
    "cannot be shipped to your selected delivery location",
    "can't be shipped to your selected delivery location",
    "cannot be delivered to your selected delivery location",
    "can't be delivered to your selected delivery location",
    "choose a different delivery location",
)
_EXPLICIT_OOS = (
    "currently unavailable",
    "temporarily out of stock",
    "out of stock",
)
_FREE_SHIPPING_CUES = ("free delivery", "free shipping")


def normalize_ascii(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", (value or "").lower().strip())
    return decomposed.encode("ascii", "ignore").decode("ascii")


def is_not_shippable(text: str | None) -> bool:
    clean = normalize_ascii(text or "")
    return any(p in clean for p in _NOT_SHIPPABLE_PATTERNS)


def looks_free_shipping(text: str | None) -> bool:
    clean = normalize_ascii(text or "")
    if is_not_shippable(text):
        return False
    return any(cue in clean for cue in _FREE_SHIPPING_CUES) or "חינם" in (text or "")


def seller_matches(blob: str, accepted: list[str]) -> bool:
    """True if any accepted seller substring appears in the seller/merchant blob.

    An empty accepted list means 'accept any seller'.
    """
    if not accepted:
        return True
    norm = normalize_ascii(blob)
    return any(normalize_ascii(str(s)) in norm for s in accepted if str(s).strip())


def _all_keywords_present(title: str, keywords: list[str]) -> str | None:
    """Return the first missing keyword, or None when all are present."""
    norm = normalize_ascii(title)
    for kw in keywords:
        k = normalize_ascii(str(kw))
        if k and k not in norm:
            return str(kw)
    return None


def _blacklisted_keyword(title: str, blacklist: list[str]) -> str | None:
    norm = normalize_ascii(title)
    for phrase in blacklist:
        p = normalize_ascii(str(phrase))
        if p and p in norm:
            return str(phrase)
    return None


def qualify_pdp(row: dict[str, Any], flt: dict[str, Any]) -> dict[str, Any]:
    """Decide in_stock for a raw PDP row given the group's filter config.

    Returns the row with derived fields: in_stock, stock_reason, price (kept only
    when qualified), seller.
    """
    price = row.get("price")
    merchant_blob = str(row.get("merchant_blob") or row.get("seller_text") or "")
    shipping_text = str(row.get("shipping_text") or "")
    explicit_oos = bool(row.get("explicit_oos"))

    accepted = list(flt.get("accepted_sellers") or [])
    require_shippable = bool(flt.get("require_shippable", True))

    seller_ok = seller_matches(merchant_blob, accepted)
    shippable_ok = (not is_not_shippable(shipping_text)) if require_shippable else True
    has_price = price is not None
    qualifies = has_price and seller_ok and shippable_ok and not explicit_oos

    if qualifies:
        reason = "confirmed_in"
    elif explicit_oos:
        reason = "explicit_oos"
    elif not has_price:
        reason = "missing_price"
    elif not seller_ok:
        reason = "seller_mismatch"
    elif not shippable_ok:
        reason = "not_shippable"
    else:
        reason = "not_qualified"

    out = dict(row)
    out["in_stock"] = bool(qualifies)
    out["stock_reason"] = reason
    out["price"] = price if qualifies else None
    out["seller"] = "pdp"
    return out


def _serp_in_stock(row: dict[str, Any], flt: dict[str, Any]) -> bool:
    price = row.get("price")
    if price is None:
        return False
    avail = normalize_ascii(str(row.get("availability_text") or ""))
    if any(term in avail for term in _EXPLICIT_OOS):
        return False
    blob = "\n".join(
        str(row.get(k) or "")
        for k in ("shipping_text", "seller_text", "availability_text")
    )
    if bool(flt.get("require_shippable", True)) and is_not_shippable(blob):
        return False
    return True


def filter_serp_rows(
    rows: list[dict[str, Any]],
    flt: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply group filters to raw SERP rows. Returns (kept_rows, reject_counts)."""
    required = list(flt.get("required_keywords") or [])
    blacklist_kw = list(flt.get("blacklist_keywords") or [])
    blacklist_asins = {str(a).strip().upper() for a in (flt.get("blacklist_asins") or [])}
    accepted = list(flt.get("accepted_sellers") or [])
    min_price = flt.get("min_price")
    max_price = flt.get("max_price")
    require_free = bool(flt.get("require_free_shipping", False))
    require_signal = bool(flt.get("require_shipping_signal", False))

    kept: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    def reject(reason: str) -> None:
        counts[reason] = counts.get(reason, 0) + 1

    for raw in rows:
        asin = (raw.get("asin") or "").strip().upper()
        title = raw.get("title") or ""
        price = raw.get("price")
        shipping_text = str(raw.get("shipping_text") or "")
        seller_blob = str(raw.get("seller_text") or "")

        if not asin:
            reject("missing_asin")
            continue
        if asin in blacklist_asins:
            reject("blacklist_asin")
            continue
        if not title:
            reject("missing_title")
            continue
        if required and _all_keywords_present(title, required) is not None:
            reject("required_keyword_missing")
            continue
        if blacklist_kw and _blacklisted_keyword(title, blacklist_kw) is not None:
            reject("blacklist_keyword")
            continue
        if price is None:
            reject("no_price")
            continue
        try:
            price_f = float(price)
        except (TypeError, ValueError):
            reject("invalid_price")
            continue
        if min_price is not None and price_f < float(min_price):
            reject("below_min_price")
            continue
        if max_price is not None and price_f > float(max_price):
            reject("above_max_price")
            continue
        if accepted and not seller_matches(seller_blob, accepted):
            reject("seller_mismatch")
            continue
        if require_free and not looks_free_shipping(shipping_text):
            reject("not_free_shipping")
            continue
        if require_signal and not (shipping_text.strip() or looks_free_shipping(shipping_text)):
            reject("no_shipping_signal")
            continue

        out = dict(raw)
        out["in_stock"] = _serp_in_stock(raw, flt)
        out["seller"] = "serp"
        kept.append(out)

    return kept, counts
