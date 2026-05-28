"""Shared scrape result type returned by the PDP and SERP scrapers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .browser import Metrics


@dataclass
class ScrapeResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    metrics: Metrics = field(default_factory=Metrics)
    captcha: bool = False
    error: str | None = None
    items_ok: int = 0
    items_skipped: int = 0

    def metrics_payload(self) -> dict[str, Any]:
        return {
            "net_kb": self.metrics.net_kb,
            "items_ok": self.items_ok,
            "items_skipped": self.items_skipped,
            "blocked_heavy": self.metrics.blocked_heavy,
        }
