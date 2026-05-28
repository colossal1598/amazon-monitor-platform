"""Machine-to-machine API (API-token auth): used by n8n and the scraper worker."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from .. import db, service
from ..schemas import JobClaim, JobResult, RunRequest
from ..security import require_api_token

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_token)])


@router.post("/runs")
def create_runs(body: RunRequest) -> dict:
    """Enqueue runs. Called by n8n cadence triggers (or manually with group_id)."""
    if body.group_id is not None:
        group = service._group_with_filter(body.group_id)
        if not group:
            return {"runs": [], "error": "group_not_found"}
        result = service.enqueue_group_run(group, trigger=body.trigger or "manual")
        return {"runs": [result] if result else []}

    due = service.find_due_groups(body.cadence)
    results = []
    for group in due:
        group["filter"] = {}
        res = service.enqueue_group_run(group, trigger=body.trigger or "scheduled")
        if res:
            results.append(res)
    return {"runs": results, "due_count": len(due)}


@router.post("/jobs/claim")
def claim_job(body: JobClaim, response: Response) -> dict:
    job = service.claim_job(body.worker_id)
    if job is None:
        response.status_code = 204
        return {}
    return job


@router.post("/jobs/{job_id}/result")
def submit_result(job_id: int, body: JobResult) -> dict:
    return service.submit_result(job_id, body.model_dump())


@router.get("/alerts/pending")
def pending_alerts(limit: int = 50) -> list[dict]:
    return db.query(
        """
        SELECT a.*, g.name AS group_name, g.notify_channel
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
