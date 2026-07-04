# n8n HTTP Node Setup (Community Edition)

Step-by-step reference for configuring every HTTP Request node when deploying on the **Windows client PC** with Docker Compose.

## Architecture (simplified)

```
scheduler (n8n) ──POST /api/jobs──► backend ◄──claim/result── worker
                                        │
                                        └──POST webhook/job-done──► process_result (n8n)
```

- **n8n never talks to the worker directly.** It enqueues jobs on the backend; the worker polls and scrapes.
- All services run in one Docker network. Use **Docker service hostnames** (`backend`, `n8n`, `wa-server`) — not `localhost` — in n8n HTTP nodes.
- **Tailscale Funnel** is only for your **Admin UI** (browser access from the internet). n8n ↔ backend traffic stays inside Docker.

---

## Community Edition: URLs vs Variables

n8n CE has no **Variables** UI (Enterprise feature). You have two options:

| Approach | When to use |
|----------|-------------|
| **Docker env + `$env.API_TOKEN`** | Default — `deploy/docker-compose.yml` sets `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` and injects `API_TOKEN`, `WA_API_KEY`, etc. |
| **Hardcode literals in each node** | If `$env.*` shows as empty/undefined in the editor — paste values directly |

**Hardcoded in workflow JSON (no `$env` needed):**

| What | Value |
|------|-------|
| Backend base URL | `http://backend:8000` |
| n8n webhook (backup poller) | `http://n8n:5678/webhook/job-done` |
| wa-server (Docker profile) | `http://wa-server:3001` |
| wa-server (on Windows host) | `http://host.docker.internal:3001` |

**Still need your secret from `deploy/.env`:**

| What | Where in `.env` |
|------|-----------------|
| `X-API-Token` header | `API_TOKEN` |
| `x-api-key` header (WhatsApp) | `WA_API_KEY` (must match `wa-server/server.js` default: `eTjW1zf2cDDZ` unless you change the code) |
| Fallback WhatsApp `to` | `WA_GROUP_ID` |

---

## Tailscale Funnel (public Admin UI on port 8443)

Funnel only allows **443**, **8443**, and **10000**. Port 8000 is **not** funnel-able.

### 1. Map backend to host port 8443

In `deploy/.env`:

```env
BACKEND_PORT=8443
```

Restart:

```powershell
cd deploy
docker compose up -d backend
```

Admin UI: `http://localhost:8443/ui/`

### 2. Enable Funnel (PowerShell as Administrator)

```powershell
tailscale funnel --https=8443 http://127.0.0.1:8443
```

Public URL (example): `https://your-pc.your-tailnet.ts.net:8443/ui/`

### 3. Security before going public

- Set strong `API_TOKEN`, `ADMIN_PASSWORD`, `POSTGRES_PASSWORD` in `.env`
- n8n (`5678`) does **not** need public exposure — keep it tailnet-only or localhost
- Re-run `docker compose up -d` after changing secrets

### 4. Backend webhook (stays internal)

`N8N_JOB_DONE_WEBHOOK_URL` must use the **Docker hostname**, not the Funnel URL:

```env
N8N_JOB_DONE_WEBHOOK_URL=http://n8n:5678/webhook/job-done
```

---

## Pre-flight checklist

Run from `deploy/` on the client PC:

```powershell
docker compose ps          # all healthy
curl http://localhost:8443/health   # or :8000 if not using 8443
```

Verify n8n container has env vars:

```powershell
docker compose exec n8n printenv API_TOKEN
docker compose exec n8n printenv BACKEND_URL
```

Expected: `BACKEND_URL=http://backend:8000`, `API_TOKEN` matches your `.env`.

Test backend from inside n8n container:

```powershell
docker compose exec n8n wget -qO- --header="X-API-Token: YOUR_TOKEN" http://backend:8000/health
```

---

## GET nodes (405 Method Not Allowed)

If GET nodes fail with **405 Method Not Allowed**, it is almost never a backend bug — the backend **does** support GET on these paths:

| Node | Method | URL (inside Docker) |
|------|--------|---------------------|
| `GET State` | **GET** | `http://backend:8000/api/state` |
| `GET Done Jobs` | **GET** | `http://backend:8000/api/jobs/done` |
| `Get Pending Alerts` | **GET** | `http://backend:8000/api/alerts/pending` |

### Common causes of 405

**1. Wrong host/port (most common)**

n8n runs **inside Docker**. Do **not** use:

| Wrong URL | Why it fails |
|-----------|--------------|
| `http://localhost:8443/api/...` | `localhost` inside the n8n container is the n8n container itself, not your PC |
| `http://localhost:8000/api/...` | Same — backend is not on n8n's localhost |
| `http://backend:8443/api/...` | Inside Docker network, backend listens on **8000**, not 8443 |
| `https://your-pc.ts.net:8443/api/...` | Funnel URL is for your **browser** only, not for n8n → backend |

**Always use:** `http://backend:8000/api/...` in every n8n HTTP node.

Port `8443` is only the **host mapping** for Tailscale Funnel / browser access. It does not change the internal Docker port.

**2. Wrong path (GET sent to a POST-only endpoint)**

| Path | Allowed methods |
|------|-----------------|
| `/api/jobs` | **POST only** — GET here → 405 |
| `/api/jobs/claim` | POST only |
| `/api/jobs/{id}/result` | POST only |
| `/api/jobs/done` | **GET** |
| `/api/state` | **GET**, PUT |
| `/api/alerts/pending` | **GET** |
| `/api/alerts/{id}/sent` | POST only |

**3. n8n node misconfiguration**

In each GET HTTP Request node, verify:

- **Method** = `GET` (not POST)
- **Send Body** = **OFF** (GET must not send a body)
- **URL** has no trailing slash (`/api/state` not `/api/state/`)
- **Authentication** = None (use `X-API-Token` header manually, not Basic Auth)

**4. Admin routes need Basic Auth, not API token**

These are **different** endpoints (for the Admin UI, not n8n workflows):

| Path | Auth | Used by n8n? |
|------|------|--------------|
| `/api/groups` | Basic Auth (`ADMIN_USER`) | No |
| `/api/dashboard/*` | Basic Auth | No |
| `/api/state`, `/api/jobs/*` | `X-API-Token` | Yes |

### GET node settings (copy exactly)

#### `GET State` (process_result workflow)

| Field | Value |
|-------|-------|
| Method | `GET` |
| URL | `http://backend:8000/api/state` |
| Query parameter | `group_key` = `={{ $json.group_key }}` |
| Header | `X-API-Token` = your `API_TOKEN` |
| Send Body | **OFF** |

#### `GET Done Jobs` (backup_poller workflow)

| Field | Value |
|-------|-------|
| Method | `GET` |
| URL | `http://backend:8000/api/jobs/done` |
| Query parameter | `limit` = `50` |
| Header | `X-API-Token` = your `API_TOKEN` |
| Send Body | **OFF** |

#### `Get Pending Alerts` (notifier workflow)

| Field | Value |
|-------|-------|
| Method | `GET` |
| URL | `http://backend:8000/api/alerts/pending` |
| Query parameter | `limit` = `50` |
| Header | `X-API-Token` = your `API_TOKEN` |
| Send Body | **OFF** |

Do **not** put `?limit=50` in the URL **and** in query params — use one or the other (query params panel is preferred).

### Test GET from inside the n8n container

On the client PC:

```powershell
docker compose exec n8n wget -qO- --header="X-API-Token: YOUR_TOKEN" "http://backend:8000/api/jobs/done?limit=5"
```

Expected: JSON array `[]` or `[{...}]`. If this works but n8n fails, the problem is in the HTTP node config (method, URL, or body).

```powershell
docker compose exec n8n wget -qO- --header="X-API-Token: YOUR_TOKEN" "http://backend:8000/api/state?group_key=demo-serp"
```

Expected: JSON array (may be empty `[]` if no products yet).

If wget returns **401** → token mismatch.  
If wget returns **405** → very unlikely; paste the full output.  
If wget **can't connect** → backend container down or wrong hostname.

---

For every HTTP Request node below, in the n8n UI:

1. **Authentication** → None (use custom headers)
2. **Send Headers** → ON
3. For POST/PUT with a body: **Send Body** → ON, **Body Content Type** → JSON, **Specify Body** → Using JSON

If the imported workflow shows an empty body, paste the **JSON body expression** from the tables below.

---

## Workflow 1: Scheduler — Enqueue Scrape Jobs

### Node: `POST Job`

| Field | Value |
|-------|-------|
| Method | `POST` |
| URL | `http://backend:8000/api/jobs` |
| Header | `X-API-Token` = your `API_TOKEN` from `.env` |
| JSON body | `={{ { group_key: $json.group_key, kind: $json.kind, payload: $json.payload, browser_profile: $json.browser_profile, attempt: $json.attempt, trigger: $json.trigger } }}` |

The `payload` object is built upstream by the **Build Jobs** code node (browser + selectors + scrape). You do not type it manually.

**Test:** Execute workflow manually → check Executions → `POST Job` should return `{ "id": ..., "status": "queued" }`.

---

## Workflow 2: Process Job Result

### Node: `GET State`

| Field | Value |
|-------|-------|
| Method | `GET` |
| URL | `http://backend:8000/api/state` |
| Query | `group_key` = `={{ $json.group_key }}` |
| Header | `X-API-Token` = your token |
| Body | none |

### Node: `PUT State`

| Field | Value |
|-------|-------|
| Method | `PUT` |
| URL | `http://backend:8000/api/state` |
| Headers | `X-API-Token`, `Content-Type: application/json` |
| JSON body | `={{ $json.state_upserts }}` |

### Node: `POST Price History`

| Field | Value |
|-------|-------|
| Method | `POST` |
| URL | `http://backend:8000/api/price_history` |
| Headers | `X-API-Token`, `Content-Type: application/json` |
| JSON body | `={{ $('Diff and Alerts').item.json.price_history }}` |

### Node: `POST Alert`

| Field | Value |
|-------|-------|
| Method | `POST` |
| URL | `http://backend:8000/api/alerts` |
| Header | `X-API-Token` |
| JSON body | `={{ $json.alert }}` |

### Node: `PATCH Processed`

| Field | Value |
|-------|-------|
| Method | `PATCH` |
| URL | `http://backend:8000/api/jobs/{{ $('Diff and Alerts').item.json.job_id }}/processed` |
| Header | `X-API-Token` |
| Body | none |

### Webhook: `Webhook Job Done`

- Path: `job-done`
- Must be **Active** (workflow toggled on) for production URL
- Production URL → paste into `.env` as `N8N_JOB_DONE_WEBHOOK_URL=http://n8n:5678/webhook/job-done`
- **Do not** use `/webhook-test/` — that only works from the editor

---

## Workflow 3: Recovery — Retry Job

Sub-workflow (called from Process Job Result). Can stay inactive but must exist.

### Node: `POST Recovery Job`

| Field | Value |
|-------|-------|
| Method | `POST` |
| URL | `http://backend:8000/api/jobs` |
| Header | `X-API-Token` |
| JSON body | `={{ { group_key: $json.group_key, kind: $json.kind, payload: $json.payload, browser_profile: $json.browser_profile, attempt: $json.attempt, trigger: $json.trigger } }}` |

---

## Workflow 4: Backup Poller (optional safety net)

### Node: `GET Done Jobs`

| Field | Value |
|-------|-------|
| Method | `GET` |
| URL | `http://backend:8000/api/jobs/done` |
| Query | `limit` = `50` |
| Header | `X-API-Token` |

### Node: `POST Process Webhook`

| Field | Value |
|-------|-------|
| Method | `POST` |
| URL | `http://n8n:5678/webhook/job-done` |
| JSON body | `={{ { job_id: $json.job_id, group_key: $json.group_key, run_id: $json.run_id, kind: $json.kind, result: $json.result } }}` |

---

## Workflow 5: Notifier (WhatsApp)

### Node: `Get Pending Alerts`

| Field | Value |
|-------|-------|
| Method | `GET` |
| URL | `http://backend:8000/api/alerts/pending?limit=50` |
| Header | `X-API-Token` |

### Node: `Send WhatsApp`

| Field | Value |
|-------|-------|
| Method | `POST` |
| URL | `http://wa-server:3001/send` (or `http://host.docker.internal:3001/send` if wa-server runs on Windows host) |
| Header | `x-api-key` = your `WA_API_KEY` |
| JSON body | `={{ { "to": $json.to, "message": $json.message, "image_url": $json.image_url } }}` |

### Nodes: `Mark Sent` / `Mark Failed`

| Field | Value |
|-------|-------|
| Method | `POST` |
| URL | `http://backend:8000/api/alerts/{{ $('Prepare Alert').item.json.alert_id }}/sent` (or `/failed`) |
| Header | `X-API-Token` |
| Body | none |

---

## `.env` values to set (copy-paste template)

```env
# Secrets — change these
API_TOKEN=your-long-random-token-here
ADMIN_USER=admin
ADMIN_PASSWORD=your-strong-password
POSTGRES_PASSWORD=your-db-password

# Public Admin UI via Tailscale Funnel
BACKEND_PORT=8443

# n8n stays on 5678 (tailnet/localhost only)
N8N_PORT=5678

# Required after activating process_result workflow
N8N_JOB_DONE_WEBHOOK_URL=http://n8n:5678/webhook/job-done

# WhatsApp (optional)
WA_API_URL=http://host.docker.internal:3001
WA_API_KEY=eTjW1zf2cDDZ
WA_GROUP_ID=9725XXXXXXXX@g.us
```

After editing `.env`:

```powershell
docker compose up -d backend n8n
```

---

## Activation order

1. Create Data Tables + seed data (`n8n/data-tables/README.md`)
2. Import workflows from `n8n/workflows/` (skip `legacy/`)
3. Re-link every Data Table node (**From list**)
4. Fix HTTP nodes per this doc (URLs, headers, bodies)
5. Activate `process_result` → copy webhook URL → set `N8N_JOB_DONE_WEBHOOK_URL` → restart backend
6. Activate: `scheduler`, `process_result`, `notifier` (if WhatsApp), `backup_poller` (optional)
7. **Do not** activate `legacy/orchestrator_*.json` (calls removed `/api/runs` endpoint)

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `401 Unauthorized` | `API_TOKEN` mismatch between `.env`, n8n container, and HTTP node header |
| URL shows `undefined/api/jobs` | `$env.BACKEND_URL` empty — use literal `http://backend:8000` |
| POST returns 422 / empty body | Enable **Send Body**, set JSON body expression from tables above |
| Jobs queue but never process | `N8N_JOB_DONE_WEBHOOK_URL` unset or `process_result` inactive |
| Webhook 404 | Workflow not active, or using `/webhook-test/` instead of `/webhook/` |
| Worker idle, jobs stuck queued | `docker compose logs worker` — check worker is running and healthy |
| WhatsApp 401 | `WA_API_KEY` must match hardcoded key in `wa-server/server.js` |
| Funnel timeout on :8000 | Use `BACKEND_PORT=8443` + `tailscale funnel --https=8443` |

---

## Verify end-to-end

```powershell
# 1. Health
curl http://localhost:8443/health

# 2. Worker polling
docker compose logs -f worker

# 3. Manual job enqueue (replace TOKEN)
curl -X POST http://localhost:8443/api/jobs `
  -H "X-API-Token: YOUR_TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"group_key":"demo-serp","kind":"serp","payload":{"browser":{},"selectors":{},"scrape":{"search_url":"https://www.amazon.com/s?k=test"}},"trigger":"manual"}'

# 4. n8n executions
# Open http://localhost:5678 → Executions → look for green runs
```

Expected flow: scheduler enqueues → worker claims → backend webhooks n8n → process_result writes state → PATCH processed.
