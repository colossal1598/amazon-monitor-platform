"""Orchestration: due-group selection, job enqueue, result processing, alerts.

This module owns the business logic so it stays testable and versioned (rather
than living inside n8n). n8n only triggers runs and ships notifications.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import db, filters
from .alerts_engine import evaluate_row
from .config import get_settings
from .logging_setup import log_event
from .schemas import GroupFilterModel
from .selectors import resolve_selectors

LOGGER = logging.getLogger("backend.service")


# --------------------------------------------------------------------------- #
# Group config loading
# --------------------------------------------------------------------------- #

def _group_with_filter(group_id: int) -> dict[str, Any] | None:
    row = db.query_one(
        """
        SELECT g.*, row_to_json(f) AS filter
        FROM scrape_group g
        LEFT JOIN group_filter f ON f.group_id = g.id
        WHERE g.id = %s
        """,
        (group_id,),
    )
    if row and row.get("filter") is None:
        row["filter"] = {}
    return row


def default_filter() -> dict[str, Any]:
    """Single source of truth for filter defaults: the GroupFilterModel schema.

    SQL column DEFAULTs in 001_init.sql are kept in sync for partial inserts/seeds.
    """
    return GroupFilterModel().model_dump()


# --------------------------------------------------------------------------- #
# Due-group selection + enqueue
# --------------------------------------------------------------------------- #

def find_due_groups(cadence: str | None) -> list[dict[str, Any]]:
    settings = get_settings()
    default_minutes = (
        settings.long_interval_minutes if cadence == "long" else settings.short_interval_minutes
    )
    params: list[Any] = [default_minutes]
    clause = "WHERE enabled = TRUE"
    if cadence in ("short", "long"):
        clause += " AND cadence = %s"
        params.insert(0, cadence)
    sql = f"""
        SELECT * FROM scrape_group
        {clause}
          AND (
            last_run_at IS NULL
            OR last_run_at <= now() - make_interval(
                mins => COALESCE(interval_minutes, %s)
            )
          )
        ORDER BY COALESCE(last_run_at, 'epoch'::timestamptz) ASC
    """
    return db.query(sql, tuple(params))


def enqueue_group_run(group: dict[str, Any], trigger: str = "scheduled") -> dict[str, Any] | None:
    """Create a run and its jobs for a group. Returns {run_id, jobs} or None if nothing to do."""
    group_id = group["id"]
    selectors = resolve_selectors(group.get("selector_profile_id"))
    nav = selectors.get("nav", {})

    jobs: list[dict[str, Any]] = []
    if group["kind"] == "pdp":
        asins = [
            r["asin"]
            for r in db.query(
                "SELECT asin FROM pdp_target WHERE group_id = %s AND enabled = TRUE ORDER BY asin",
                (group_id,),
            )
        ]
        if asins:
            jobs.append(
                {
                    "kind": "pdp",
                    "payload": {
                        "asins": asins,
                        "selectors": selectors.get("pdp", {}),
                        "nav": nav,
                        "headless": bool(group.get("headless", True)),
                        "max_concurrent": int(group.get("max_concurrent", 2)),
                    },
                }
            )
    else:
        targets = db.query(
            "SELECT * FROM serp_target WHERE group_id = %s AND enabled = TRUE ORDER BY id",
            (group_id,),
        )
        for t in targets:
            jobs.append(
                {
                    "kind": "serp",
                    "payload": {
                        "search_url": t["search_url"],
                        "scrape_mode": t["scrape_mode"],
                        "max_pages": int(t["max_pages"]),
                        "selectors": selectors.get("serp", {}),
                        "nav": nav,
                        "headless": bool(group.get("headless", True)),
                        "serp_target_id": t["id"],
                    },
                }
            )

    if not jobs:
        log_event(LOGGER, logging.INFO, "run_skipped_no_targets", group_id=group_id)
        return None

    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO run (group_id, status, trigger) VALUES (%s, 'running', %s) RETURNING id",
                (group_id, trigger),
            )
            run_id = cur.fetchone()["id"]
            cur.execute(
                """
                INSERT INTO run_metric (run_id, duration_sec, net_kb, items_scraped,
                                        items_ok, items_skipped, captcha, alerts_emitted, blocked_heavy)
                VALUES (%s, 0, 0, 0, 0, 0, 0, 0, 0)
                """,
                (run_id,),
            )
            for j in jobs:
                cur.execute(
                    """
                    INSERT INTO job (group_id, run_id, kind, payload, status)
                    VALUES (%s, %s, %s, %s, 'queued') RETURNING id
                    """,
                    (group_id, run_id, j["kind"], json.dumps(j["payload"])),
                )
                j["id"] = cur.fetchone()["id"]
            cur.execute("UPDATE scrape_group SET last_run_at = now() WHERE id = %s", (group_id,))

    log_event(
        LOGGER, logging.INFO, "run_enqueued",
        group_id=group_id, run_id=run_id, jobs=len(jobs), kind=group["kind"],
    )
    return {"run_id": run_id, "jobs": len(jobs), "group_id": group_id}


# --------------------------------------------------------------------------- #
# Worker job queue
# --------------------------------------------------------------------------- #

def _requeue_expired_leases() -> None:
    db.execute(
        """
        UPDATE job SET status = 'queued', claimed_by = NULL, lease_expires_at = NULL
        WHERE status = 'claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at < now()
        """
    )


def claim_job(worker_id: str) -> dict[str, Any] | None:
    """Atomically claim the oldest queued job (FOR UPDATE SKIP LOCKED)."""
    _requeue_expired_leases()
    lease = get_settings().job_lease_seconds
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH next AS (
                    SELECT id FROM job
                    WHERE status = 'queued'
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE job
                SET status = 'claimed', claimed_by = %s, claimed_at = now(),
                    attempts = attempts + 1,
                    lease_expires_at = now() + make_interval(secs => %s)
                FROM next
                WHERE job.id = next.id
                RETURNING job.id, job.group_id, job.run_id, job.kind, job.payload
                """,
                (worker_id, lease),
            )
            row = cur.fetchone()
    return row


# --------------------------------------------------------------------------- #
# Result processing
# --------------------------------------------------------------------------- #

def _bump_run_metric(run_id: int, **deltas: float) -> None:
    if not deltas:
        return
    sets = ", ".join(f"{k} = {k} + %({k})s" for k in deltas)
    db.execute(f"UPDATE run_metric SET {sets} WHERE run_id = %(run_id)s", {**deltas, "run_id": run_id})


def _process_rows(
    group: dict[str, Any],
    rows: list[dict[str, Any]],
) -> int:
    """Update product_state + price_history and create alerts. Returns alerts emitted."""
    flt = {**default_filter(), **(group.get("filter") or {})}
    group_id = group["id"]

    if group["kind"] == "pdp":
        observed = [filters.qualify_pdp(r, flt) for r in rows if not r.get("_skip_update")]
    else:
        observed, _counts = filters.filter_serp_rows(rows, flt)

    alerts_emitted = 0
    with db.connection() as conn:
        for row in observed:
            asin = (row.get("asin") or "").strip().upper()
            if not asin:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM product_state WHERE group_id = %s AND asin = %s",
                    (group_id, asin),
                )
                prev = cur.fetchone()

                decision = evaluate_row(row, prev, flt)
                price = row.get("price")
                in_stock = bool(row.get("in_stock"))
                title = row.get("title") or (prev or {}).get("title")
                image_url = row.get("image_url") or (prev or {}).get("image_url")
                product_url = row.get("product_url") or f"https://www.amazon.com/dp/{asin}"

                cur.execute(
                    """
                    INSERT INTO product_state
                        (group_id, asin, title, seller, price, in_stock, image_url, product_url,
                         first_seen, last_seen)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                    ON CONFLICT (group_id, asin) DO UPDATE SET
                        title = COALESCE(EXCLUDED.title, product_state.title),
                        seller = EXCLUDED.seller,
                        price = COALESCE(EXCLUDED.price, product_state.price),
                        in_stock = EXCLUDED.in_stock,
                        image_url = COALESCE(EXCLUDED.image_url, product_state.image_url),
                        product_url = COALESCE(EXCLUDED.product_url, product_state.product_url),
                        last_seen = now()
                    """,
                    (group_id, asin, title, row.get("seller"), price, in_stock, image_url, product_url),
                )
                cur.execute(
                    "INSERT INTO price_history (group_id, asin, price, in_stock) VALUES (%s, %s, %s, %s)",
                    (group_id, asin, price, in_stock),
                )

                if decision.emit:
                    old_price = (prev or {}).get("price")
                    cur.execute(
                        """
                        INSERT INTO alert
                            (group_id, asin, alert_type, title, old_price, new_price,
                             image_url, product_url, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                        """,
                        (
                            group_id, asin, decision.alert_type, title, old_price, price,
                            image_url, product_url,
                        ),
                    )
                    alerts_emitted += 1
    return alerts_emitted


def submit_result(job_id: int, result: dict[str, Any]) -> dict[str, Any]:
    """Process a worker's scrape result for one job and finalize the run if complete."""
    job = db.query_one("SELECT * FROM job WHERE id = %s", (job_id,))
    if job is None:
        return {"ok": False, "error": "job_not_found"}

    group = _group_with_filter(job["group_id"]) or {}
    rows = result.get("rows") or []
    metrics = result.get("metrics") or {}
    captcha = 1 if result.get("captcha") else 0

    alerts_emitted = 0
    error = result.get("error")
    try:
        if not error:
            alerts_emitted = _process_rows(group, rows)
        status = "failed" if error else "done"
        db.execute(
            "UPDATE job SET status = %s, result = %s, error = %s, finished_at = now() WHERE id = %s",
            (status, json.dumps({"rows": len(rows), "metrics": metrics}), error, job_id),
        )
    except Exception as exc:  # noqa: BLE001
        log_event(LOGGER, logging.ERROR, "result_processing_failed", job_id=job_id, error=str(exc))
        db.execute(
            "UPDATE job SET status = 'failed', error = %s, finished_at = now() WHERE id = %s",
            (str(exc), job_id),
        )
        error = str(exc)

    run_id = job["run_id"]
    if run_id is not None:
        ok = int(metrics.get("items_ok", len(rows)))
        skipped = int(metrics.get("items_skipped", 0))
        _bump_run_metric(
            run_id,
            net_kb=float(metrics.get("net_kb", 0) or 0),
            items_scraped=len(rows),
            items_ok=ok,
            items_skipped=skipped,
            captcha=captcha,
            alerts_emitted=alerts_emitted,
            blocked_heavy=int(metrics.get("blocked_heavy", 0) or 0),
        )
        _maybe_finalize_run(run_id)

    log_event(
        LOGGER, logging.INFO, "job_result_processed",
        job_id=job_id, group_id=job["group_id"], run_id=run_id,
        rows=len(rows), alerts=alerts_emitted, captcha=captcha, error=error,
    )
    return {"ok": error is None, "alerts_emitted": alerts_emitted}


def _maybe_finalize_run(run_id: int) -> None:
    pending = db.query_one(
        "SELECT count(*) AS n FROM job WHERE run_id = %s AND status IN ('queued', 'claimed')",
        (run_id,),
    )
    if pending and pending["n"] > 0:
        return
    failed = db.query_one(
        "SELECT count(*) AS n FROM job WHERE run_id = %s AND status = 'failed'", (run_id,)
    )
    status = "error" if (failed and failed["n"] > 0) else "done"
    db.execute(
        """
        UPDATE run SET status = %s, finished_at = now()
        WHERE id = %s AND finished_at IS NULL
        """,
        (status, run_id),
    )
    db.execute(
        """
        UPDATE run_metric SET duration_sec = EXTRACT(EPOCH FROM (
            SELECT finished_at - started_at FROM run WHERE id = %s
        )) WHERE run_id = %s
        """,
        (run_id, run_id),
    )
    log_event(LOGGER, logging.INFO, "run_finalized", run_id=run_id, status=status)
    _maybe_push_alerts_webhook()


def _maybe_push_alerts_webhook() -> None:
    url = get_settings().n8n_alerts_webhook_url.strip()
    if not url:
        return
    pending = db.query_one("SELECT count(*) AS n FROM alert WHERE status = 'pending'")
    if not pending or pending["n"] == 0:
        return
    try:
        import urllib.request

        req = urllib.request.Request(
            url,
            data=json.dumps({"pending": pending["n"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as exc:  # noqa: BLE001
        log_event(LOGGER, logging.WARNING, "alerts_webhook_failed", error=str(exc))
