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
from .browser import make_rate_limiter
from .config import Config, load_config
from .logging_setup import log_event, setup_logging
from .pdp import scrape_pdp
from .result import ScrapeResult
from .serp import scrape_serp

LOGGER = logging.getLogger("worker.main")


def _dispatch(job: dict[str, Any], config: Config, rate_limiter: Any) -> ScrapeResult:
    kind = job.get("kind")
    payload = job.get("payload") or {}
    if kind == "pdp":
        return scrape_pdp(payload, proxy_url=config.proxy_url, rate_limiter=rate_limiter)
    if kind == "serp":
        return scrape_serp(payload, proxy_url=config.proxy_url, rate_limiter=rate_limiter)
    return ScrapeResult(error=f"unknown job kind: {kind!r}")


def _result_payload(result: ScrapeResult) -> dict[str, Any]:
    return {
        "rows": result.rows,
        "metrics": result.metrics_payload(),
        "captcha": result.captcha,
        "error": result.error,
    }


def _process_job(job: dict[str, Any], config: Config, client: ApiClient, rate_limiter: Any) -> None:
    job_id = job.get("id")
    ctx = {
        "job_id": job_id,
        "group_id": job.get("group_id"),
        "run_id": job.get("run_id"),
        "kind": job.get("kind"),
    }
    log_event(LOGGER, logging.INFO, "job claimed", **ctx)

    try:
        result = _dispatch(job, config, rate_limiter)
    except Exception as exc:
        log_event(LOGGER, logging.ERROR, "job scrape crashed", error=str(exc), **ctx)
        result = ScrapeResult(error=f"worker_exception: {exc}")

    try:
        client.submit_result(int(job_id), _result_payload(result))
        log_event(
            LOGGER,
            logging.INFO,
            "job completed",
            items_ok=result.items_ok,
            items_skipped=result.items_skipped,
            captcha=result.captcha,
            error=result.error,
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
    rate_limiter = make_rate_limiter(config.max_requests_per_minute)

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
            _process_job(job, config, client, rate_limiter)
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
