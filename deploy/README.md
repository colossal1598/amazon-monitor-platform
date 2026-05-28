# Deployment & Runbook

Docker Compose stack for the n8n Scraper Platform.

## Services

| Service     | Port | Role |
|-------------|------|------|
| `postgres`  | 5432 | Source of truth: config, state, history, jobs, metrics (+ n8n's own DB) |
| `backend`   | 8000 | FastAPI: config API, job queue, diff/alert/filter engine, admin UI at `/ui/` |
| `worker`    | -    | Stateless Playwright scraper; pulls jobs from the backend |
| `n8n`       | 5678 | Orchestrator: schedules runs, delivers alerts via WhatsApp |
| `wa-server` | 3001 | (optional, `--profile whatsapp`) WhatsApp delivery bridge |

## Quickstart

```bash
cd deploy
cp .env.example .env          # then edit secrets (API_TOKEN, ADMIN_PASSWORD, ...)
docker compose up -d --build
```

Then open:
- Admin UI: http://localhost:8000/ui/  (Basic auth: `ADMIN_USER` / `ADMIN_PASSWORD`)
- n8n editor: http://localhost:5678/  (same Basic auth)
- API health: http://localhost:8000/health

## Wire up n8n (one time)

1. Open n8n, go to **Workflows -> Import from File**.
2. Import all three files from `n8n/workflows/`:
   - `orchestrator_short.json` (triggers `short` cadence groups)
   - `orchestrator_long.json` (triggers `long` cadence groups)
   - `notifier.json` (delivers pending alerts via WhatsApp)
3. **Activate** each workflow (toggle top-right).

The orchestrators call `POST /api/runs`; the backend decides which groups are
actually due based on each group's cadence/interval, so triggering frequently is safe.

## WhatsApp delivery

Two options:

- **Run wa-server on the host** (recommended; keeps your existing WhatsApp session):
  start it as you do today on port 3001. n8n reaches it via
  `WA_API_URL=http://host.docker.internal:3001`. Set `WA_API_KEY` and `WA_GROUP_ID` in `.env`.
- **Containerize it**: `docker compose --profile whatsapp up -d --build wa-server`,
  then scan the QR from `docker compose logs -f wa-server`. The session persists in
  the `wadata` volume.

A group's `notify_channel` overrides `WA_GROUP_ID` for that group's alerts.

## Production: mobile IP

Amazon blocks datacenter IPs. For a stable residential/mobile IP, run the worker
on the client PC instead of in Docker (see `worker/README.md`):

```bash
docker compose up -d --scale worker=0      # disable the docker worker
# on the client PC, point the worker at this backend's public URL + API_TOKEN
```

Alternatively, set `PROXY_URL` in `.env` to a residential/mobile proxy and keep the
docker worker.

## Selector hotfix (Amazon changed the DOM)

- Edit the active **Selector Profile** in the admin UI (no restart needed; the worker
  receives selectors with each job), or
- Set `SELECTOR_PROFILE_JSON` in `.env` to override globally and `docker compose up -d backend`.

## Operations

- Logs (structured JSON): `docker compose logs -f backend worker n8n`
- Reset everything (DANGER, wipes data): `docker compose down -v`
- Scale workers: `docker compose up -d --scale worker=3`

## Deploying on Coolify (Israeli server) — pipeline validation run

Coolify runs on a datacenter IP, so Amazon will likely captcha the scraper. Use this
run to validate the control plane end-to-end (n8n -> queue -> worker -> state ->
alerts -> dashboards/logs), not scrape quality.

1. In Coolify, create a new **Docker Compose** resource pointing at this repo.
   - Base directory: repo root. Compose file: `deploy/docker-compose.yml`.
2. Set environment variables (Coolify -> Environment Variables). At minimum:
   - `API_TOKEN` (long random), `ADMIN_USER`, `ADMIN_PASSWORD`
   - `POSTGRES_PASSWORD`
   - `SEED_DEMO_GROUP=true`  (creates a harmless demo SERP group so a run happens immediately)
   - Leave `WA_*` empty for the validation run — alerts will queue as `pending`/`failed`
     and are still visible in the dashboard, so WhatsApp isn't required to validate.
3. Deploy. Coolify builds `backend`, `worker`, and starts `postgres` + `n8n`.
   (Skip the `whatsapp` profile for this run.)
4. Expose the `backend` (8000) and `n8n` (5678) ports/domains via Coolify.
5. Open the admin UI (`/ui/`), confirm the demo group exists, then import + activate
   the three n8n workflows (see `n8n/README.md`).
6. Watch it work:
   - `docker logs` (or Coolify logs) for `backend`/`worker` — structured JSON events
     (`run_enqueued`, `job claimed`, `job_result_processed`, `run_finalized`).
   - Admin dashboard: runs table + charts populate; products/alerts appear (or you see
     `captcha` counts, which is the expected datacenter-IP outcome).

When you're ready for real scrapes: either run the worker on the mobile-IP PC pointing
at the Coolify backend URL (and scale the Coolify worker to 0), or set `PROXY_URL` to a
residential/mobile proxy.

## Troubleshooting

- **401 from API**: check `API_TOKEN` (machine calls) or Basic-auth creds (UI/n8n).
- **No runs happening**: ensure a group is `enabled`, has targets, and the
  orchestrator workflows are **active** in n8n.
- **Captcha spikes**: you're likely on a datacenter IP - use the client-PC worker or a proxy.
- **n8n can't reach wa-server**: confirm `WA_API_URL`; on Linux the `host-gateway`
  mapping is set for `host.docker.internal`.
