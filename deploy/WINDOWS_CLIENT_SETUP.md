# Windows Client PC Setup

Step-by-step guide for running the **entire Amazon Scraper Platform on one Windows machine** — backend, Postgres, Playwright worker, n8n, and optional WhatsApp (`wa-server`).

This layout is ideal when the PC has a **residential/mobile IP**: the Docker worker scrapes Amazon from that IP, so you avoid datacenter captchas without paying for a proxy.

**Prerequisites on this PC**

| Requirement | Notes |
|-------------|-------|
| Windows 10 or 11 (64-bit) | Home or Pro |
| [Tailscale](https://tailscale.com/download/windows) | Already installed and signed in (for remote admin access) |
| [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) | Enable WSL 2 backend when prompted; start Docker Desktop before each session |
| [Git for Windows](https://git-scm.com/download/win) | Use **Git Bash** or PowerShell |

---

## 1. Clone the repository

Open **PowerShell** (or Git Bash):

```powershell
cd $HOME
git clone https://github.com/YOUR_ORG/amazon-scraper-platform.git
cd amazon-scraper-platform
```

Replace the URL with your actual repo remote.

---

## 2. Create and edit `.env`

```powershell
cd deploy
Copy-Item .env.example .env
notepad .env
```

Docker Compose reads `deploy/.env` automatically. Set at least the secrets below before first boot.

### Environment variables (reference)

| Variable | Required | Description |
|----------|----------|-------------|
| **Postgres** | | |
| `POSTGRES_USER` | yes | DB user (default `scraper`) |
| `POSTGRES_PASSWORD` | **change** | DB password — pick a strong value |
| `POSTGRES_DB` | yes | Main app database name (default `scraper`) |
| `POSTGRES_PORT` | no | Host port for Postgres (default `5432`). Leave as-is unless it conflicts |
| **Auth** | | |
| `API_TOKEN` | **change** | Shared secret for worker ↔ backend and n8n ↔ backend (`X-API-Token` header). Use a long random string |
| `ADMIN_USER` | yes | Basic-auth username for Admin UI and n8n editor |
| `ADMIN_PASSWORD` | **change** | Basic-auth password for Admin UI and n8n editor |
| **Ports** | | |
| `BACKEND_PORT` | no | Host port for backend / Admin UI (default `8000`) |
| `N8N_PORT` | no | Host port for n8n editor (default `5678`) |
| `WA_PORT` | no | Host port for wa-server when using Docker profile (default `3001`) |
| **Worker** | | |
| `PROXY_URL` | no | Leave **empty** on this PC to use the machine's residential IP. Set only if you use an external proxy |
| `HEADLESS` | no | `true` for Docker worker (default) |
| `POLL_INTERVAL_SECONDS` | no | Seconds between job polls when idle (default `5`) |
| `MAX_REQUESTS_PER_MINUTE` | no | Rate limit for page loads (default `10`) |
| `WORKER_ID` | no | Label in logs (default `docker-worker-1`) |
| **WhatsApp** | | |
| `WA_API_URL` | if alerts | `http://wa-server:3001` (Docker profile) or `http://host.docker.internal:3001` (wa-server on Windows host) |
| `WA_API_KEY` | if alerts | Must match wa-server's API key |
| `WA_GROUP_ID` | if alerts | Default WhatsApp JID when a group has no `notify_channel` (e.g. `9725XXXXXXXX@g.us`) |
| `WA_SERVER_PATH` | no | Path to vendored `wa-server` folder (default `../wa-server`) |
| **n8n webhook** | | |
| `N8N_JOB_DONE_WEBHOOK_URL` | **yes after n8n setup** | Production webhook URL from n8n (see §5). Backend POSTs here when a scrape job finishes |
| **Misc** | | |
| `LOG_LEVEL` | no | `INFO` or `DEBUG` |
| `TZ` | no | Timezone for n8n schedules (default `Asia/Jerusalem`) |
| `SEED_DEMO_GROUP` | no | `true` creates a harmless demo SERP group on first boot (good for smoke tests) |
| `SELECTOR_PROFILE_JSON` | no | Emergency selector hotfix (JSON string); normally edit profiles in the Admin UI |

---

## 3. Start the stack

From `deploy/`:

```powershell
docker compose up -d --build
```

First run downloads images and builds `backend` and `worker` — allow several minutes.

Check that services are healthy:

```powershell
docker compose ps
```

Expected URLs on **this PC**:

| Service | URL |
|---------|-----|
| Admin UI | http://localhost:8000/ui/ |
| API health | http://localhost:8000/health |
| n8n editor | http://localhost:5678/ |

Log in with `ADMIN_USER` / `ADMIN_PASSWORD`.

---

## 4. Tailscale remote access

### Option A — Tailnet only (private, recommended for n8n)

Tailscale lets you open the Admin UI and n8n from your phone or another PC without port-forwarding on the router.

1. On the client PC, open the Tailscale app and note the machine's **Tailscale IP** (e.g. `100.x.y.z`) or **MagicDNS** name (e.g. `client-pc.tailnet-name.ts.net`).
2. From another device on the same tailnet:
   - Admin UI: `http://100.x.y.z:8000/ui/` (or `http://client-pc.tailnet-name.ts.net:8000/ui/`)
   - n8n: `http://100.x.y.z:5678/`

### Option B — Tailscale Funnel (public Admin UI)

Funnel exposes services to the **public internet**. It only works on ports **443**, **8443**, and **10000** — not 8000.

1. In `deploy/.env`, set:

   ```env
   BACKEND_PORT=8443
   ```

2. Restart the backend:

   ```powershell
   docker compose up -d backend
   ```

3. Enable Funnel (PowerShell as Administrator):

   ```powershell
   tailscale funnel --https=8443 http://127.0.0.1:8443
   ```

4. Open the public URL: `https://your-pc.your-tailnet.ts.net:8443/ui/`

5. **Keep n8n on 5678** — do not funnel it unless you add a reverse proxy. n8n ↔ backend traffic stays inside Docker (`http://backend:8000`, `http://n8n:5678/webhook/job-done`).

See **[N8N_HTTP_SETUP.md](N8N_HTTP_SETUP.md)** for every n8n HTTP node URL, header, and body.

### Security notes

- **Change defaults** before exposing anything: `API_TOKEN`, `ADMIN_PASSWORD`, and `POSTGRES_PASSWORD`.
- Tailnet access: only tailnet members can reach ports; do **not** publish 8000/5678 to the public internet without Funnel + HTTPS.
- Funnel: Admin UI is public — use a strong `ADMIN_PASSWORD`.
- Postgres (`5432`) is bound to localhost by default via Docker port mapping — keep it that way.
- Anyone with `API_TOKEN` can enqueue jobs and read/write orchestration data; treat it like a root password.
- n8n and the Admin UI share the same Basic-auth credentials — use a strong password.

---

## 5. n8n first-time setup

n8n is the **orchestrator**: it schedules scrapes, processes job results, applies filters, and sends alerts. The backend is a thin queue + storage layer.

### 5.1 Import workflows

1. Open n8n → **Workflows** → **Import from File**.
2. Import every JSON file in the repo folder `n8n/workflows/`.
3. Workflows import as **inactive** — leave them inactive until Data Tables exist (next step).

### 5.2 Create Data Tables

Follow **[n8n/data-tables/README.md](../n8n/data-tables/README.md)** to create the required n8n Data Tables (`groups`, `serp_targets`, `pdp_targets`, `group_filters`, `selector_profiles`, `browser_profiles`) and seed from `n8n/data-tables/seed/`.

The `group_key` values in Data Tables must match what workflows send to `POST /api/jobs`.

### 5.3 Configure the job-done webhook

1. Open **`Process Job Result`** (`process_result.json` — Webhook path `job-done`).
2. Open the **Webhook** node and copy its **Production URL** (starts with your n8n base URL + `/webhook/...`).
3. Paste it into `deploy/.env`:

   ```env
   N8N_JOB_DONE_WEBHOOK_URL=http://n8n:5678/webhook/your-path
   ```

   For the URL n8n shows in the editor, use the **internal** form if the backend calls from Docker: replace `localhost` with `n8n` so the backend container can reach it, e.g. `http://n8n:5678/webhook/job-done`.

4. Restart the backend so it picks up the new env:

   ```powershell
   docker compose up -d backend
   ```

### 5.4 Activate workflows

1. Open each imported workflow.
2. (Optional) **Execute workflow** once manually to verify backend / WhatsApp connectivity.
3. Toggle **Active** (top-right) on:
   - `scheduler.json` — enqueues jobs via `POST /api/jobs`
   - `process_result.json` — webhook job processor
   - `backup_poller.json` — optional safety net for missed webhooks
   - `notifier.json` — if using WhatsApp alerts

> **Legacy note:** `orchestrator_short.json` and `orchestrator_long.json` call the removed `POST /api/runs` endpoint. Do **not** activate them in the n8n-centric setup — use the newer scheduler workflows from the same `n8n/workflows/` folder instead.

---

## 6. WhatsApp (`wa-server`) on Windows

Alerts are optional. Skip this section if you only want scraping + dashboard.

### Option A — Run on the Windows host (recommended)

Keeps an existing WhatsApp session on the PC and makes QR re-scan easier.

1. Install Node.js LTS on Windows.
2. From the repo:

   ```powershell
   cd wa-server
   npm install
   $env:WA_API_KEY = "your-secret-key"
   node server.js
   ```

3. Scan the QR code printed in the terminal.
4. In `deploy/.env`:

   ```env
   WA_API_URL=http://host.docker.internal:3001
   WA_API_KEY=your-secret-key
   WA_GROUP_ID=your-whatsapp-group-jid
   ```

5. Restart n8n:

   ```powershell
   docker compose up -d n8n
   ```

### Option B — Run in Docker (`whatsapp` profile)

```powershell
cd deploy
docker compose --profile whatsapp up -d --build wa-server
docker compose logs -f wa-server
```

Scan the QR from the logs. Session persists in the `wadata` volume.

In `.env`, keep `WA_API_URL=http://wa-server:3001` (default).

---

## 7. Worker on the same PC (residential IP)

The `worker` service starts automatically with `docker compose up -d`. It:

- Polls `http://backend:8000/api/jobs/claim` inside the Docker network
- Launches headless Chromium with Playwright
- Uses the **host's outbound IP** (your residential/mobile connection) when `PROXY_URL` is empty

This is the main reason to run everything on the client PC instead of a cloud VPS.

To watch worker activity:

```powershell
docker compose logs -f worker
```

To temporarily stop the worker:

```powershell
docker compose stop worker
```

To run multiple workers:

```powershell
docker compose up -d --scale worker=2
```

---

## 8. Verify end-to-end

1. **Health:** `curl http://localhost:8000/health` → `{"status":"ok"}` (or open in a browser).
2. **Admin UI:** http://localhost:8000/ui/ — confirm groups/targets (or Data Table–driven state via API).
3. **n8n:** Open **Executions** — scheduler and webhook workflows should show green runs after a few minutes.
4. **Logs:**

   ```powershell
   docker compose logs -f backend worker n8n
   ```

   Look for JSON events such as `job claimed`, `job_result_processed`, `run_finalized`.

5. **WhatsApp:** If configured, pending alerts should move to `sent` in the dashboard after the notifier runs.

---

## 9. Troubleshooting

### Containers won't start

```powershell
docker compose ps
docker compose logs postgres
docker compose logs backend
```

- Ensure Docker Desktop is running (whale icon in the system tray).
- Port conflict? Change `BACKEND_PORT` or `N8N_PORT` in `.env` and run `docker compose up -d` again.

### `401 Unauthorized` from API

- Machine calls (worker, n8n HTTP nodes): check `API_TOKEN` matches in `.env`, n8n container env, and worker container env.
- Browser (Admin UI): use `ADMIN_USER` / `ADMIN_PASSWORD`.

### No scrape jobs running

- Data Tables: at least one group `enabled = true` with targets (see `n8n/data-tables/README.md`).
- n8n scheduler workflow is **Active**.
- `docker compose logs worker` — worker should poll every few seconds.

### Webhook not firing

- `N8N_JOB_DONE_WEBHOOK_URL` set and backend restarted after editing `.env`.
- URL must be reachable **from inside the backend container** — use `http://n8n:5678/webhook/...`, not `localhost`.
- Check backend logs for `job_done_webhook_failed`.

### Captcha spikes in metrics

- Amazon may still rate-limit; lower `MAX_REQUESTS_PER_MINUTE`.
- Confirm you're not also running a cloud worker on a datacenter IP against the same backend.

### n8n can't reach wa-server

- Host wa-server: `WA_API_URL=http://host.docker.internal:3001` and wa-server listening on `0.0.0.0:3001`.
- Docker wa-server: `docker compose --profile whatsapp ps` and check logs for QR / ready state.
- `WA_API_KEY` must match on both sides.

### WhatsApp QR / session issues

- Host: delete `wa-server/.wwebjs_auth` only if you intend to re-pair (loses session).
- Docker: `docker compose --profile whatsapp down` then remove volume `amazon-scraper-platform_wadata` only when re-pairing.

### Reset everything (destructive)

Wipes Postgres, n8n, and WhatsApp session data:

```powershell
docker compose down -v
```

---

## 10. Later: move to Linux or cloud

The same `deploy/docker-compose.yml` works on Linux VPS, Coolify, etc. Typical changes:

| On client PC (now) | On cloud server (later) |
|--------------------|-------------------------|
| Worker in Docker (residential IP) | `docker compose up -d --scale worker=0` on server |
| `BACKEND_URL` internal in compose | Worker on PC uses `BACKEND_URL=https://your-server.example.com` + same `API_TOKEN` |
| Tailscale IP for admin | Coolify / reverse proxy for 8000 and 5678 |
| `N8N_JOB_DONE_WEBHOOK_URL=http://n8n:5678/...` | Same pattern if n8n stays in compose; use public URL only if n8n is external |

See **[deploy/README.md](README.md)** for Coolify and proxy-worker options.

---

## Quick reference

```powershell
# Daily start (Docker Desktop must be running)
cd $HOME\amazon-scraper-platform\deploy
docker compose up -d

# Rebuild after git pull
docker compose up -d --build

# Follow logs
docker compose logs -f backend worker n8n

# Stop stack (keep data)
docker compose down
```
