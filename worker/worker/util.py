"""Shared selector access, parsing, and normalization helpers.

Selector profiles arrive in each job payload. These helpers read a selector key
from that dict and tolerate missing/blank keys by falling back to baked-in
defaults so a partial profile never crashes the scrape.
"""

from __future__ import annotations

import html
import re
from typing import Any, Iterable

_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
_PRICE_RE = re.compile(r"\$?\s*([0-9][0-9,]*)(?:\.(\d{2}))?")
_MONEY_RE = re.compile(r"\$?\s*([0-9]+(?:[.,][0-9]{1,2})?)(?!\s*[kK])")

NETWORK_ERROR_PATTERNS = (
    "err_network_access_denied",
    "err_network_changed",
    "err_connection_refused",
    "err_connection_reset",
    "err_connection_timed_out",
    "err_internet_disconnected",
    "net::err_",
)


def sel_list(selectors: dict[str, Any], key: str, default: Iterable[str]) -> list[str]:
    """Return the selector list at ``key`` (or ``default`` if missing/empty)."""
    value = selectors.get(key) if isinstance(selectors, dict) else None
    if isinstance(value, (list, tuple)):
        out = [str(s) for s in value if str(s).strip()]
        if out:
            return out
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return [str(s) for s in default]


def sel_str(selectors: dict[str, Any], key: str, default: str) -> str:
    """Return the single selector string at ``key`` (or ``default``)."""
    value = selectors.get(key) if isinstance(selectors, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (list, tuple)) and value:
        first = str(value[0]).strip()
        if first:
            return first
    return default


def valid_asin(value: str | None) -> bool:
    return bool(value and _ASIN_RE.match(value.strip().upper()))


def normalize_asin(value: str | None) -> str:
    return (value or "").strip().upper()


def is_network_error(error: Exception) -> bool:
    err_str = str(error).lower()
    return any(p in err_str for p in NETWORK_ERROR_PATTERNS)


def parse_price_text(text: str | None) -> float | None:
    """Parse a single ``$1,234.56`` style price from PDP buy-box text."""
    m = _PRICE_RE.search(text or "")
    if not m:
        return None
    dollars = m.group(1).replace(",", "")
    cents = m.group(2) or "00"
    try:
        return float(f"{dollars}.{cents}")
    except ValueError:
        return None


def card_list_price(text: str | None, *, min_price: float = 5.0) -> float | None:
    """Largest money amount >= ``min_price`` (avoids star ratings / micro-prices)."""
    if not text:
        return None
    cleaned = html.unescape(text).replace("\xa0", " ").strip().replace(",", "")
    amounts: list[float] = []
    for g in _MONEY_RE.findall(cleaned):
        try:
            amounts.append(float(g))
        except ValueError:
            continue
    ok = [v for v in amounts if v >= min_price]
    return max(ok) if ok else None


def pick_amazon_image_url(candidates: Any, rank: int = 1) -> str | None:
    """Return the URL at ``rank`` after sorting HTTP candidates by length descending."""
    if isinstance(candidates, dict):
        urls = list(candidates.keys())
    elif isinstance(candidates, (list, tuple, set)):
        urls = list(candidates)
    else:
        return None
    http_urls = [u for u in urls if isinstance(u, str) and u.startswith("http")]
    if not http_urls:
        return None
    sorted_urls = sorted(http_urls, key=len, reverse=True)
    idx = min(rank, len(sorted_urls) - 1)
    return sorted_urls[idx]
