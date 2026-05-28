"""Worker configuration sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    backend_url: str
    api_token: str
    worker_id: str
    proxy_url: str | None
    headless: bool
    poll_interval_seconds: int
    log_level: str
    max_requests_per_minute: int

    @property
    def claim_url(self) -> str:
        return f"{self.backend_url}/api/jobs/claim"

    def result_url(self, job_id: int) -> str:
        return f"{self.backend_url}/api/jobs/{job_id}/result"


def load_config() -> Config:
    backend_url = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
    proxy_url = os.getenv("PROXY_URL") or None
    return Config(
        backend_url=backend_url,
        api_token=os.getenv("API_TOKEN", ""),
        worker_id=os.getenv("WORKER_ID", "worker-1"),
        proxy_url=proxy_url,
        headless=_env_bool("HEADLESS", True),
        poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 5),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        max_requests_per_minute=_env_int("MAX_REQUESTS_PER_MINUTE", 10),
    )
