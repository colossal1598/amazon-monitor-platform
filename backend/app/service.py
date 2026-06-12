"""Thin orchestration: job queue, run lifecycle, webhooks. Business logic lives in n8n."""

from __future__ import annotations

import json
import logging
from typing import Any

from . import db
from .config import get_settings
from .logging_setup import log_event

LOGGER = logging.getLogger("backend.service")


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
                RETURNING job.id, job.group_id, job.group_key, job.run_id, job.kind,
                          job.payload, job.browser_profile, job.attempt
                """,
                (worker_id, lease),
            )
            row = cur.fetchone()
    return row


def create_run(group_key: str, trigger: str = "manual") -> dict[str, Any]:
    """Insert a run row and its empty run_metric row."""
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run (group_key, status, trigger)
                VALUES (%s, 'running', %s) RETURNING id
                """,
                (group_key, trigger),
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
    log_event(LOGGER, logging.INFO, "run_created", run_id=run_id, group_key=group_key, trigger=trigger)
    return {"run_id": run_id, "group_key": group_key}


def create_job(body: dict[str, Any]) -> dict[str, Any]:
    """Insert a queued job. Creates a run when trigger is set and run_id is omitted."""
    run_id = body.get("run_id")
    trigger = body.get("trigger")
    group_key = body["group_key"]
    if run_id is None and trigger:
        run_id = create_run(group_key, trigger)["run_id"]

    row = db.query_one(
        """
        INSERT INTO job (group_key, run_id, kind, payload, browser_profile, attempt, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'queued')
        RETURNING id, run_id, group_key, kind, status
        """,
        (
            group_key,
            run_id,
            body["kind"],
            json.dumps(body["payload"]),
            body.get("browser_profile"),
            body.get("attempt", 1),
        ),
    )
    log_event(
        LOGGER,
        logging.INFO,
        "job_created",
        job_id=row["id"],
        run_id=run_id,
        group_key=group_key,
        kind=body["kind"],
    )
    return row


def _bump_run_metric(run_id: int, **deltas: float) -> None:
    if not deltas:
        return
    sets = ", ".join(f"{k} = {k} + %({k})s" for k in deltas)
    db.execute(f"UPDATE run_metric SET {sets} WHERE run_id = %(run_id)s", {**deltas, "run_id": run_id})


def persist_job_result(job_id: int, result: dict[str, Any]) -> dict[str, Any]:
    """Store raw worker result, update run metrics, finalize run, fire webhook."""
    job = db.query_one("SELECT * FROM job WHERE id = %s", (job_id,))
    if job is None:
        return {"ok": False, "error": "job_not_found"}

    rows = result.get("rows") or []
    metrics = result.get("metrics") or {}
    captcha = 1 if result.get("captcha") else 0
    error = result.get("error")
    status = "failed" if error else "done"

    try:
        db.execute(
            """
            UPDATE job
            SET status = %s, result = %s, error = %s, finished_at = now(),
                scrape_quality = %s,
                browser_profile = COALESCE(%s, browser_profile),
                attempt = COALESCE(%s, attempt),
                n8n_processed = FALSE
            WHERE id = %s
            """,
            (
                status,
                json.dumps(result),
                error,
                result.get("scrape_quality"),
                result.get("browser_profile"),
                result.get("attempt"),
                job_id,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log_event(LOGGER, logging.ERROR, "result_persist_failed", job_id=job_id, error=str(exc))
        db.execute(
            "UPDATE job SET status = 'failed', error = %s, finished_at = now() WHERE id = %s",
            (str(exc), job_id),
        )
        return {"ok": False, "error": str(exc)}

    run_id = job.get("run_id")
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
            blocked_heavy=int(metrics.get("blocked_heavy", 0) or 0),
        )
        finalize_run(run_id)

    updated = db.query_one("SELECT * FROM job WHERE id = %s", (job_id,)) or job
    if status == "done":
        _fire_job_done_webhook(updated, result)

    log_event(
        LOGGER,
        logging.INFO,
        "job_result_persisted",
        job_id=job_id,
        group_key=job.get("group_key"),
        run_id=run_id,
        rows=len(rows),
        captcha=captcha,
        error=error,
    )
    return {"ok": error is None}


def finalize_run(run_id: int) -> None:
    """Mark run done/error when all jobs are finished."""
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


def _fire_job_done_webhook(job: dict[str, Any], result: dict[str, Any]) -> None:
    url = get_settings().n8n_job_done_webhook_url.strip()
    if not url:
        return
    payload = {
        "job_id": job["id"],
        "group_key": job.get("group_key"),
        "run_id": job.get("run_id"),
        "kind": job.get("kind"),
        "scrape_quality": result.get("scrape_quality"),
        "captcha": result.get("captcha", False),
        "attempt": result.get("attempt"),
        "browser_profile": result.get("browser_profile"),
        "result": result,
    }
    try:
        import urllib.request

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as exc:  # noqa: BLE001
        log_event(LOGGER, logging.WARNING, "job_done_webhook_failed", job_id=job["id"], error=str(exc))
