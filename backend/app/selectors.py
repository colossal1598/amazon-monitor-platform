"""Selector-profile loading, seeding, and env override.

Selectors are data, not code. The default profile is seeded from
seed/default_selector_profile.json on startup. Operators can edit profiles in
the admin UI, or hotfix without a DB write via SELECTOR_PROFILE_JSON.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import db
from .config import get_settings

LOGGER = logging.getLogger("backend.selectors")

SEED_PATH = Path(__file__).resolve().parent.parent / "seed" / "default_selector_profile.json"


def load_seed() -> dict[str, Any]:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def seed_default_profile() -> None:
    """Insert the default profile if no default exists yet."""
    existing = db.query_one("SELECT id FROM selector_profile WHERE is_default = TRUE LIMIT 1")
    if existing:
        return
    seed = load_seed()
    db.execute(
        """
        INSERT INTO selector_profile (name, marketplace, locale, version, selectors, is_default)
        VALUES (%(name)s, %(marketplace)s, %(locale)s, %(version)s, %(selectors)s, TRUE)
        ON CONFLICT (name) DO NOTHING
        """,
        {
            "name": seed["name"],
            "marketplace": seed.get("marketplace", "amazon.com"),
            "locale": seed.get("locale", "en-IL"),
            "version": seed.get("version", 1),
            "selectors": json.dumps(seed["selectors"]),
        },
    )
    LOGGER.info("Seeded default selector profile", extra={"context": {"name": seed["name"]}})


def _env_override() -> dict[str, Any] | None:
    raw = get_settings().selector_profile_json.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        # Accept either a bare selectors object or a full profile wrapper.
        return data.get("selectors", data) if isinstance(data, dict) else None
    except json.JSONDecodeError as exc:
        LOGGER.warning("Invalid SELECTOR_PROFILE_JSON ignored", extra={"context": {"error": str(exc)}})
        return None


def resolve_selectors(profile_id: int | None) -> dict[str, Any]:
    """Return the effective selector dict for a group.

    Precedence: SELECTOR_PROFILE_JSON env override > group's profile > default.
    """
    override = _env_override()
    if override is not None:
        return override

    row = None
    if profile_id is not None:
        row = db.query_one("SELECT selectors FROM selector_profile WHERE id = %s", (profile_id,))
    if row is None:
        row = db.query_one("SELECT selectors FROM selector_profile WHERE is_default = TRUE LIMIT 1")
    if row is None:
        return load_seed()["selectors"]
    return row["selectors"]
