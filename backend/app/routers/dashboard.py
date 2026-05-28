"""Dashboard/read API for the admin UI (Basic-auth protected)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import db
from ..security import require_admin

router = APIRouter(prefix="/api/dashboard", dependencies=[Depends(require_admin)])


@router.get("/summary")
def summary() -> dict:
    groups = db.query_one("SELECT count(*) AS n FROM scrape_group WHERE enabled = TRUE")
    products = db.query_one("SELECT count(*) AS n FROM product_state")
    in_stock = db.query_one("SELECT count(*) AS n FROM product_state WHERE in_stock = TRUE")
    pending = db.query_one("SELECT count(*) AS n FROM alert WHERE status = 'pending'")
    sent_24h = db.query_one(
        "SELECT count(*) AS n FROM alert WHERE status = 'sent' AND sent_at > now() - interval '24 hours'"
    )
    queued = db.query_one("SELECT count(*) AS n FROM job WHERE status IN ('queued','claimed')")
    return {
        "active_groups": groups["n"] if groups else 0,
        "tracked_products": products["n"] if products else 0,
        "in_stock": in_stock["n"] if in_stock else 0,
        "alerts_pending": pending["n"] if pending else 0,
        "alerts_sent_24h": sent_24h["n"] if sent_24h else 0,
        "jobs_in_flight": queued["n"] if queued else 0,
    }


@router.get("/runs")
def recent_runs(limit: int = 50) -> list[dict]:
    return db.query(
        """
        SELECT r.id, r.group_id, g.name AS group_name, r.status, r.trigger,
               r.started_at, r.finished_at, r.error,
               m.duration_sec, m.net_kb, m.items_scraped, m.items_ok,
               m.items_skipped, m.captcha, m.alerts_emitted, m.blocked_heavy
        FROM run r
        LEFT JOIN scrape_group g ON g.id = r.group_id
        LEFT JOIN run_metric m ON m.run_id = r.id
        ORDER BY r.started_at DESC
        LIMIT %s
        """,
        (limit,),
    )


@router.get("/metrics/timeseries")
def metrics_timeseries(group_id: int | None = None, limit: int = 100) -> list[dict]:
    where = "WHERE m.duration_sec IS NOT NULL"
    params: list = []
    if group_id is not None:
        where += " AND r.group_id = %s"
        params.append(group_id)
    params.append(limit)
    rows = db.query(
        f"""
        SELECT r.id AS run_id, r.group_id, r.started_at, m.duration_sec, m.net_kb,
               m.items_ok, m.items_skipped, m.alerts_emitted, m.captcha
        FROM run r JOIN run_metric m ON m.run_id = r.id
        {where}
        ORDER BY r.started_at DESC
        LIMIT %s
        """,
        tuple(params),
    )
    return list(reversed(rows))


@router.get("/alerts")
def alerts_history(limit: int = 100) -> list[dict]:
    return db.query(
        """
        SELECT a.*, g.name AS group_name
        FROM alert a LEFT JOIN scrape_group g ON g.id = a.group_id
        ORDER BY a.created_at DESC
        LIMIT %s
        """,
        (limit,),
    )


@router.get("/products")
def products(group_id: int | None = None, limit: int = 200) -> list[dict]:
    where = ""
    params: list = []
    if group_id is not None:
        where = "WHERE group_id = %s"
        params.append(group_id)
    params.append(limit)
    return db.query(
        f"SELECT * FROM product_state {where} ORDER BY last_seen DESC LIMIT %s",
        tuple(params),
    )


@router.get("/price_history")
def price_history(group_id: int, asin: str, limit: int = 200) -> list[dict]:
    return list(
        reversed(
            db.query(
                """
                SELECT price, in_stock, observed_at FROM price_history
                WHERE group_id = %s AND asin = %s
                ORDER BY observed_at DESC LIMIT %s
                """,
                (group_id, asin.upper(), limit),
            )
        )
    )
