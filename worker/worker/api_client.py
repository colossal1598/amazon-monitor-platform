"""HTTP client for the backend job API."""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from .config import Config
from .logging_setup import log_event

LOGGER = logging.getLogger("worker.api")


class ApiClient:
    def __init__(self, config: Config, *, timeout: float = 30.0) -> None:
        self._config = config
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-API-Token": config.api_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def claim_job(self) -> Optional[dict[str, Any]]:
        """Claim the next job. Returns the job dict, or ``None`` when there is no work."""
        resp = self._session.post(
            self._config.claim_url,
            json={"worker_id": self._config.worker_id},
            timeout=self._timeout,
        )
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        if not resp.content:
            return None
        job = resp.json()
        return job if isinstance(job, dict) else None

    def submit_result(self, job_id: int, result: dict[str, Any]) -> None:
        resp = self._session.post(
            self._config.result_url(job_id),
            json=result,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        log_event(
            LOGGER,
            logging.INFO,
            "result submitted",
            job_id=job_id,
            status=resp.status_code,
        )
