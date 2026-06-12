"""Environment-driven configuration for the backend."""

from __future__ import annotations

import os
from functools import lru_cache


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.database_url: str = os.getenv(
            "DATABASE_URL",
            "postgresql://scraper:scraper@localhost:5432/scraper",
        )
        # Token presented by the worker and n8n on machine-to-machine calls.
        self.api_token: str = os.getenv("API_TOKEN", "changeme-token")
        # Basic-auth credentials for the admin UI + its config API.
        self.admin_user: str = os.getenv("ADMIN_USER", "admin")
        self.admin_password: str = os.getenv("ADMIN_PASSWORD", "admin")
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

        # Job lease: if a worker dies, a claimed job is requeued after this many seconds.
        self.job_lease_seconds: int = int(os.getenv("JOB_LEASE_SECONDS", "300"))

        # Optional inline selector-profile override (JSON string). Lets you hotfix
        # selectors via env without touching the DB when Amazon changes the DOM.
        self.selector_profile_json: str = os.getenv("SELECTOR_PROFILE_JSON", "")

        # n8n webhook fired when a job completes (primary notification path).
        self.n8n_job_done_webhook_url: str = os.getenv("N8N_JOB_DONE_WEBHOOK_URL", "")

        # Legacy: optional push of pending-alert count on run completion.
        self.n8n_alerts_webhook_url: str = os.getenv("N8N_ALERTS_WEBHOOK_URL", "")

        self.seed_demo_group: bool = _bool(os.getenv("SEED_DEMO_GROUP"), False)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
