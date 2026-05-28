"""Diff/alert engine: compares scrape rows against product_state and emits alerts.

Ported from alert_decisions.py + state_engine.py, generalized to operate per
group. Decisions are pure; persistence is done by the caller's DB connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

AlertType = Literal["new_product", "back_in_stock", "price_drop"]

# Per-(group, asin) priority when multiple alerts fire in one run.
_PRIORITY = {"back_in_stock": 3, "new_product": 2, "price_drop": 1}


@dataclass(frozen=True)
class AlertDecision:
    emit: bool
    alert_type: AlertType | None
    skip_reason: str | None


def decide_new_product(is_first_observation: bool) -> AlertDecision:
    if is_first_observation:
        return AlertDecision(True, "new_product", None)
    return AlertDecision(False, None, "SKIP_NOT_NEW_ASIN")


def decide_back_in_stock(old_stock: int, new_stock: int) -> AlertDecision:
    if old_stock == 0 and new_stock == 1:
        return AlertDecision(True, "back_in_stock", None)
    return AlertDecision(False, None, "SKIP_STOCK_UNCHANGED")


def decide_price_drop(
    old_price: float | None,
    new_price: float | None,
    threshold_pct: float,
) -> AlertDecision:
    if old_price is None or new_price is None:
        return AlertDecision(False, None, "SKIP_MISSING_PRICE")
    if old_price <= 0:
        return AlertDecision(False, None, "SKIP_INVALID_OLD_PRICE")
    if new_price >= old_price:
        return AlertDecision(False, None, "SKIP_PRICE_NOT_DOWN")
    pct = ((old_price - new_price) / old_price) * 100
    if pct < threshold_pct:
        return AlertDecision(False, None, "SKIP_BELOW_THRESHOLD")
    return AlertDecision(True, "price_drop", None)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_row(
    row: dict[str, Any],
    prev: dict[str, Any] | None,
    flt: dict[str, Any],
) -> AlertDecision:
    """Decide the single highest-priority alert (if any) for one observed row.

    ``prev`` is the existing product_state row (or None for first observation).
    Out-of-stock observations never alert; they only update state.
    """
    in_stock = bool(row.get("in_stock"))
    new_price = _to_float(row.get("price"))
    threshold = float(flt.get("price_drop_percent", 10) or 10)

    candidates: list[AlertDecision] = []

    if prev is None:
        if in_stock and bool(flt.get("alert_new", True)):
            candidates.append(decide_new_product(True))
    else:
        old_stock = 1 if prev.get("in_stock") else 0
        new_stock = 1 if in_stock else 0
        old_price = _to_float(prev.get("price"))

        if bool(flt.get("alert_back_in_stock", True)):
            d = decide_back_in_stock(old_stock, new_stock)
            if d.emit:
                candidates.append(d)
        if in_stock and bool(flt.get("alert_price_drop", True)):
            d = decide_price_drop(old_price, new_price, threshold)
            if d.emit:
                candidates.append(d)

    if not candidates:
        return AlertDecision(False, None, "NO_ALERT")
    best = max(candidates, key=lambda d: _PRIORITY.get(d.alert_type or "", 0))
    return best
