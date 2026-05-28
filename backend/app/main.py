"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import get_settings
from .logging_setup import setup_logging
from .routers import config as config_router
from .routers import dashboard as dashboard_router
from .routers import orchestration as orchestration_router
from .selectors import seed_default_profile

LOGGER = logging.getLogger("backend.main")

UI_DIR = Path(__file__).resolve().parent.parent.parent / "admin-ui"


def _seed_demo_group() -> None:
    existing = db.query_one("SELECT id FROM scrape_group LIMIT 1")
    if existing:
        return
    profile = db.query_one("SELECT id FROM selector_profile WHERE is_default = TRUE LIMIT 1")
    pid = profile["id"] if profile else None
    grp = db.query_one(
        """
        INSERT INTO scrape_group (name, kind, niche, cadence, selector_profile_id)
        VALUES ('Demo SERP', 'serp', 'demo', 'long', %s) RETURNING id
        """,
        (pid,),
    )
    gid = grp["id"]
    db.execute(
        """
        INSERT INTO group_filter (group_id, required_keywords, price_drop_percent)
        VALUES (%s, '[]', 10)
        """,
        (gid,),
    )
    db.execute(
        """
        INSERT INTO serp_target (group_id, search_url, label, scrape_mode, max_pages)
        VALUES (%s, 'https://www.amazon.com/s?k=usb+c+cable', 'demo search', 'newest_front', 1)
        """,
        (gid,),
    )
    LOGGER.info("Seeded demo group", extra={"context": {"group_id": gid}})


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    db.wait_for_db()
    db.init_pool()
    db.run_migrations()
    seed_default_profile()
    if settings.seed_demo_group:
        _seed_demo_group()
    LOGGER.info("Backend ready")
    yield
    db.close_pool()


app = FastAPI(title="n8n Scraper Platform API", version="2.0.0", lifespan=lifespan)

app.include_router(config_router.router)
app.include_router(dashboard_router.router)
app.include_router(orchestration_router.router)


@app.get("/health")
def health() -> dict:
    try:
        db.query_one("SELECT 1 AS ok")
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
