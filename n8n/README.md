# n8n Scraper Platform — Orchestration Workflows

n8n is the **orchestrator** for the Amazon scraper platform. It owns scheduling, result processing (filter/diff/alerts), recovery retries, and WhatsApp delivery. The backend is a thin job queue + persistence layer; the worker is stateless.

## Workflows

| File | Trigger | What it does |
| --- | --- | --- |
| `workflows/scheduler.json` | Schedule, every **1 minute** | Reads Data Tables → enqueues due groups → `POST /api/jobs` per target |
| `workflows/process_result.json` | Webhook `POST /webhook/job-done` | Filters rows, diffs state, writes alerts, recovery branch, `PATCH /api/jobs/{id}/processed` |
| `workflows/recovery.json` | Sub-workflow (Execute Workflow) | Re-enqueues group targets with `recovery` browser profile, `attempt+1` |
| `workflows/backup_poller.json` | Schedule, every **5 minutes** | `GET /api/jobs/done` → replays unprocessed jobs into the process webhook |
| `workflows/notifier.json` | Schedule, every **1 minute** | `GET /api/alerts/pending` → WhatsApp → mark `sent`/`failed` |
| `workflows/legacy/` | — | Deprecated `orchestrator_*.json` (old `POST /api/runs` cadence model) |

## Data Tables

Configuration lives in n8n Data Tables, not the backend. See **[data-tables/README.md](data-tables/README.md)** for schemas and seed files under `data-tables/seed/`.

Tables: `groups`, `serp_targets`, `pdp_targets`, `group_filters`, `selector_profiles`, `browser_profiles`.

After import, open each workflow and re-select Data Tables **From list** if node references show as broken.

## Required environment variables

Set as n8n process/container environment variables (read in expressions via `{{ $env.NAME }}`).

| Variable | Example | Used by | Purpose |
| --- | --- | --- | --- |
| `BACKEND_URL` | `http://backend:8000` | all | Backend API base URL (no trailing slash) |
| `API_TOKEN` | `secret-token` | all | `X-API-Token` header on backend calls |
| `WA_API_URL` | `http://wa-server:3001` | notifier | WhatsApp `wa-server` base URL |
| `WA_API_KEY` | `…` | notifier | `x-api-key` header for `wa-server` |
| `WA_GROUP_ID` | `9725…@c.us` | notifier | Fallback WhatsApp JID when alert has no `notify_channel` |
| `N8N_WEBHOOK_URL` | `http://n8n:5678/webhook/job-done` | backup_poller | Production URL of `process_result.json` webhook (optional; defaults to in-stack URL) |

> n8n exposes `$env` unless `N8N_BLOCK_ENV_ACCESS_IN_NODE=true`. Use n8n Variables as fallback if blocked.

## Webhook configuration

1. Import and **activate** `process_result.json`.
2. Open the **Webhook Job Done** node → copy the **Production URL** (path `job-done`).
3. Set in `deploy/.env`:

   ```env
   N8N_JOB_DONE_WEBHOOK_URL=http://n8n:5678/webhook/job-done
   ```

4. Restart the backend so it POSTs job results to n8n on completion.

The backup poller replays the same payload shape if the primary webhook fails.

## Import steps

1. **Create Data Tables** per [data-tables/README.md](data-tables/README.md).
2. **Seed rows** from `data-tables/seed/` (browser profiles, selector profile, demo group).
   - For `selector_profiles.selectors_json`, stringify the `selectors` object from `backend/seed/default_selector_profile.json`.
3. **Import workflows** — n8n → **⋯** → **Import from File**, or:

   ```bash
   n8n import:workflow --separate --input=./n8n/workflows
   ```

4. **Re-link Data Table nodes** (select each table **From list**).
5. **Set environment variables** and restart n8n.
6. **Activate** (in order): `recovery.json` (sub-workflow, can stay inactive but must exist), `process_result.json`, `scheduler.json`, `notifier.json`, `backup_poller.json` (optional safety net).
7. Copy **Webhook Production URL** → `N8N_JOB_DONE_WEBHOOK_URL` → restart backend.

## API contracts

Backend (`X-API-Token` on all requests):

| Method | Path | Body / query |
| --- | --- | --- |
| `POST` | `/api/jobs` | `{ group_key, kind, payload, browser_profile?, attempt?, trigger? }` |
| `GET` | `/api/state?group_key=` | — |
| `PUT` | `/api/state` | `[ProductStateUpsert, …]` |
| `POST` | `/api/price_history` | `[{ group_key, asin, price, in_stock }, …]` |
| `POST` | `/api/alerts` | `{ group_key, asin, alert_type, title, old_price, new_price, image_url, product_url, notify_channel }` |
| `PATCH` | `/api/jobs/{id}/processed` | — |
| `GET` | `/api/jobs/done?limit=50` | Unprocessed done jobs |
| `GET` | `/api/alerts/pending?limit=50` | Pending alerts for notifier |

**Webhook payload** (backend → `process_result.json`):

```json
{
  "job_id": 42,
  "group_key": "demo-serp",
  "run_id": 7,
  "kind": "serp",
  "result": {
    "rows": [],
    "scrape_quality": "ok",
    "captcha": false,
    "metrics": {},
    "browser_profile": "fast",
    "attempt": 1
  }
}
```

**Job payload** (n8n → worker via `/api/jobs`):

```json
{
  "browser": { "profile": "fast", "block_heavy": true, "headless": true, "channel": "chrome" },
  "selectors": { "nav": {}, "pdp": {}, "serp": {} },
  "scrape": { "search_url": "…", "scrape_mode": "newest_front", "max_pages": 1 }
}
```

## Error handling

- HTTP nodes use `retryOnFail`, `maxTries: 3`, `waitBetweenTries: 2000`.
- `onError: continueRegularOutput` on non-critical paths (scheduler, state writes).
- Notifier **Send WhatsApp** uses `continueErrorOutput` → `sent` / `failed` branches.
- `process_result.json` calls **Recovery** sub-workflow when `scrape_quality !== 'ok'` and `attempt < 3`.

## Node versions (2025/2026)

- `scheduleTrigger` typeVersion **1.2**
- `httpRequest` typeVersion **4.2**
- `webhook` typeVersion **2**
- `splitOut` typeVersion **1**
- `set` typeVersion **3.4** (`assignments.assignments[]`)
- `code` typeVersion **2**
- `dataTable` typeVersion **1** (`returnAll` for full table reads)
- `executeWorkflow` typeVersion **1.2** / `executeWorkflowTrigger` **1.1**
