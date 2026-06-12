"""Worker entrypoint: poll the backend, scrape, and submit normalized results.

The loop is defensive: any scrape failure is reported back as a result with
``error`` set rather than crashing the worker.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .api_client import ApiClient
from .browser import BrowserConfig, make_rate_limiter, parse_browser_config
from .config import Config, load_config
from .logging_setup import log_event, setup_logging
from .pdp import scrape_pdp
from .result import ScrapeResult
from .serp import scrape_serp

LOGGER = logging.getLogger("worker.main")

RETRY_PROFILE = "retry"
_RETRY_QUALITIES = frozenset({"captcha", "empty", "parse_failed"})


def _normalize_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy top-level fields into the new job envelope."""
    browser_raw = raw.get("browser") if isinstance(raw.get("browser"), dict) else {}
    scrape_raw = raw.get("scrape") if isinstance(raw.get("scrape"), dict) else {}
    selectors = raw.get("selectors") if isinstance(raw.get("selectors"), dict) else {}

    nav = selectors.get("nav") if isinstance(selectors.get("nav"), dict) else {}
    if not nav and isinstance(raw.get("nav"), dict):
        nav = raw["nav"]

    scrape: dict[str, Any] = {
        "asins": scrape_raw.get("asins") if scrape_raw.get("asins") is not None else raw.get("asins") or [],
        "search_url": scrape_raw.get("search_url") or raw.get("search_url") or "",
        "scrape_mode": scrape_raw.get("scrape_mode") or raw.get("scrape_mode") or "featured_full",
        "max_pages": scrape_raw.get("max_pages", raw.get("max_pages", 1)),
        "max_concurrent": scrape_raw.get("max_concurrent", raw.get("max_concurrent", 2)),
    }

    browser = dict(browser_raw)
    if "headless" not in browser and "headless" in raw:
        browser["headless"] = raw["headless"]

    envelope = {
        "browser": browser,
        "selectors": selectors,
        "scrape": scrape,
    }
    if nav:
        envelope["nav"] = nav
    return envelope


def _job_rate_limiter(config: Config, browser_config: BrowserConfig) -> Any:
    rpm = browser_config.rate_limit_rpm
    if rpm is not None and rpm > 0:
        return make_rate_limiter(rpm)
    return make_rate_limiter(config.max_requests_per_minute)


def _dispatch_scrape(
    kind: str | None,
    envelope: dict[str, Any],
    browser_config: BrowserConfig,
    config: Config,
    *,
    attempt: int,
) -> ScrapeResult:
    rate_limiter = _job_rate_limiter(config, browser_config)
    selectors = envelope.get("selectors") or {}
    scrape = envelope.get("scrape") or {}
    if kind == "pdp":
        return scrape_pdp(
            browser_config,
            scrape,
            selectors,
            rate_limiter=rate_limiter,
            attempt=attempt,
        )
    if kind == "serp":
        return scrape_serp(
            browser_config,
            scrape,
            selectors,
            rate_limiter=rate_limiter,
            attempt=attempt,
        )
    return ScrapeResult(error=f"unknown job kind: {kind!r}")


def _dispatch_with_retry(job: dict[str, Any], config: Config) -> ScrapeResult:
    kind = job.get("kind")
    raw_payload = job.get("payload") or {}
    envelope = _normalize_payload(raw_payload)

    fast_payload = {
        **envelope,
        "browser": {**envelope.get("browser", {}), "profile": "fast"},
        "nav": envelope.get("nav"),
    }
    fast_config = parse_browser_config(
        fast_payload,
        env_headless=config.headless,
        env_proxy_url=config.proxy_url,
        profile_override="fast",
    )
    result = _dispatch_scrape(kind, envelope, fast_config, config, attempt=1)

    if result.scrape_quality not in _RETRY_QUALITIES:
        return result

    log_event(
        LOGGER,
        logging.INFO,
        "retrying scrape with retry profile",
        kind=kind,
        scrape_quality=result.scrape_quality,
        job_id=job.get("id"),
    )
    retry_payload = {
        **envelope,
        "browser": {**envelope.get("browser", {}), "profile": RETRY_PROFILE},
        "nav": envelope.get("nav"),
    }
    retry_config = parse_browser_config(
        retry_payload,
        env_headless=config.headless,
        env_proxy_url=config.proxy_url,
        profile_override=RETRY_PROFILE,
    )
    return _dispatch_scrape(kind, envelope, retry_config, config, attempt=2)


def _process_job(job: dict[str, Any], config: Config, client: ApiClient) -> None:
    job_id = job.get("id")
    ctx = {
        "job_id": job_id,
        "group_id": job.get("group_id"),
        "run_id": job.get("run_id"),
        "kind": job.get("kind"),
    }
    log_event(LOGGER, logging.INFO, "job claimed", **ctx)

    try:
        result = _dispatch_with_retry(job, config)
    except Exception as exc:
        log_event(LOGGER, logging.ERROR, "job scrape crashed", error=str(exc), **ctx)
        result = ScrapeResult(error=f"worker_exception: {exc}")

    try:
        client.submit_result(int(job_id), result.result_payload())
        log_event(
            LOGGER,
            logging.INFO,
            "job completed",
            items_ok=result.items_ok,
            items_skipped=result.items_skipped,
            captcha=result.captcha,
            error=result.error,
            scrape_quality=result.scrape_quality,
            browser_profile=result.browser_profile,
            attempt=result.attempt,
            timing_ms=result.timing_ms,
            net_kb=result.metrics.net_kb,
            blocked_heavy=result.metrics.blocked_heavy,
            **ctx,
        )
    except Exception as exc:
        log_event(LOGGER, logging.ERROR, "result submission failed", error=str(exc), **ctx)


def run() -> None:
    config = load_config()
    setup_logging(config.log_level)
    client = ApiClient(config)

    log_event(
        LOGGER,
        logging.INFO,
        "worker started",
        worker_id=config.worker_id,
        backend_url=config.backend_url,
        proxy=bool(config.proxy_url),
        headless=config.headless,
        max_requests_per_minute=config.max_requests_per_minute,
    )

    while True:
        try:
            job = client.claim_job()
        except requests.RequestException as exc:
            log_event(LOGGER, logging.WARNING, "claim failed", error=str(exc))
            time.sleep(config.poll_interval_seconds)
            continue
        except Exception as exc:
            log_event(LOGGER, logging.ERROR, "claim crashed", error=str(exc))
            time.sleep(config.poll_interval_seconds)
            continue

        if job is None:
            time.sleep(config.poll_interval_seconds)
            continue

        try:
            _process_job(job, config, client)
        except Exception as exc:
            log_event(LOGGER, logging.ERROR, "job loop crashed", error=str(exc))
            time.sleep(config.poll_interval_seconds)


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        log_event(LOGGER, logging.INFO, "worker stopped")


if __name__ == "__main__":
    main()
