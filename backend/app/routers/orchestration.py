"""Machine-to-machine API (API-token auth): used by n8n and the scraper worker."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from .. import db, service
from ..schemas import (
    AlertCreate,
    JobClaim,
    JobCreate,
    JobResult,
    PriceHistoryCreate,
    ProductStateUpsert,
)
from ..security import require_api_token

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_token)])


@router.post("/jobs")
def create_job(body: JobCreate) -> dict:
    return service.create_job(body.model_dump())


@router.post("/jobs/claim")
def claim_job(body: JobClaim, response: Response) -> dict:
    job = service.claim_job(body.worker_id)
    if job is None:
        response.status_code = 204
        return {}
    return job


@router.post("/jobs/{job_id}/result")
def submit_result(job_id: int, body: JobResult) -> dict:
    return service.persist_job_result(job_id, body.model_dump())


@router.get("/jobs/done")
def list_done_jobs(limit: int = 50) -> list[dict]:
    return db.query(
        """
        SELECT id, group_id, group_key, run_id, kind, status, result, error,
               browser_profile, scrape_quality, attempt, n8n_processed,
               created_at, finished_at
        FROM job
        WHERE status = 'done' AND n8n_processed = FALSE
        ORDER BY finished_at ASC NULLS LAST, id ASC
        LIMIT %s
        """,
        (limit,),
    )


@router.patch("/jobs/{job_id}/processed")
def mark_job_processed(job_id: int) -> dict:
    updated = db.query_one(
        """
        UPDATE job SET n8n_processed = TRUE
        WHERE id = %s AND status = 'done'
        RETURNING id
        """,
        (job_id,),
    )
    if not updated:
        raise HTTPException(404, "job not found or not in done state")
    return {"ok": True}


@router.get("/state")
def list_state(group_key: str, limit: int = 500) -> list[dict]:
    return db.query(
        """
        SELECT * FROM product_state
        WHERE group_key = %s
        ORDER BY last_seen DESC
        LIMIT %s
        """,
        (group_key, limit),
    )


@router.put("/state")
def upsert_state(items: list[ProductStateUpsert]) -> dict:
    upserted = 0
    with db.connection() as conn:
        for item in items:
            asin = item.asin.strip().upper()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE product_state SET
                        title = COALESCE(%s, title),
                        seller = %s,
                        price = COALESCE(%s, price),
                        in_stock = %s,
                        image_url = COALESCE(%s, image_url),
                        product_url = COALESCE(%s, product_url),
                        group_id = COALESCE(%s, group_id),
                        last_seen = now()
                    WHERE group_key = %s AND asin = %s
                    """,
                    (
                        item.title,
                        item.seller,
                        item.price,
                        item.in_stock,
                        item.image_url,
                        item.product_url,
                        item.group_id,
                        item.group_key,
                        asin,
                    ),
                )
                if cur.rowcount > 0:
                    upserted += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO product_state
                        (group_key, group_id, asin, title, seller, price, in_stock,
                         image_url, product_url, first_seen, last_seen)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                    """,
                    (
                        item.group_key,
                        item.group_id,  # nullable when using group_key only (n8n-centric)
                        asin,
                        item.title,
                        item.seller,
                        item.price,
                        item.in_stock,
                        item.image_url,
                        item.product_url or f"https://www.amazon.com/dp/{asin}",
                    ),
                )
                upserted += 1
    return {"ok": True, "upserted": upserted}


@router.post("/price_history")
def create_price_history(items: list[PriceHistoryCreate]) -> dict:
    inserted = 0
    with db.connection() as conn:
        for item in items:
            asin = item.asin.strip().upper()
            group_id = item.group_id
            if group_id is None:
                row = db.query_one(
                    "SELECT group_id FROM product_state WHERE group_key = %s AND asin = %s",
                    (item.group_key, asin),
                )
                group_id = row["group_id"] if row else None
            if group_id is None:
                row = db.query_one(
                    "SELECT group_id FROM product_state WHERE group_key = %s LIMIT 1",
                    (item.group_key,),
                )
                group_id = row["group_id"] if row else None
            if group_id is None:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO price_history (group_id, asin, price, in_stock)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (group_id, asin, item.price, item.in_stock),
                )
                inserted += 1
    return {"ok": True, "inserted": inserted}


@router.post("/alerts")
def create_alert(body: AlertCreate) -> dict:
    asin = body.asin.strip().upper()
    row = db.query_one(
        """
        INSERT INTO alert
            (group_id, group_key, asin, alert_type, title, old_price, new_price,
             image_url, product_url, notify_channel, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
        RETURNING id
        """,
        (
            body.group_id,
            body.group_key,
            asin,
            body.alert_type,
            body.title,
            body.old_price,
            body.new_price,
            body.image_url,
            body.product_url,
            body.notify_channel,
        ),
    )
    return {"ok": True, "id": row["id"]}


@router.get("/alerts/pending")
def pending_alerts(limit: int = 50) -> list[dict]:
    return db.query(
        """
        SELECT a.*, COALESCE(g.name, a.group_key) AS group_name
        FROM alert a
        LEFT JOIN scrape_group g ON g.id = a.group_id
        WHERE a.status = 'pending'
        ORDER BY a.created_at ASC
        LIMIT %s
        """,
        (limit,),
    )


@router.post("/alerts/{alert_id}/sent")
def mark_sent(alert_id: int) -> dict:
    db.execute(
        "UPDATE alert SET status = 'sent', sent_at = now() WHERE id = %s", (alert_id,)
    )
    return {"ok": True}


@router.post("/alerts/{alert_id}/failed")
def mark_failed(alert_id: int) -> dict:
    db.execute("UPDATE alert SET status = 'failed' WHERE id = %s", (alert_id,))
    return {"ok": True}


@router.post("/runs", deprecated=True)
def create_runs_deprecated() -> None:
    raise HTTPException(
        status_code=410,
        detail="POST /api/runs removed; enqueue jobs via POST /api/jobs from n8n",
    )
