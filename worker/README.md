# n8n Scraper Platform — Worker

A stateless, long-running Playwright worker. It polls the backend for scrape
jobs, scrapes Amazon using a **selector profile supplied in each job** (nothing
is hardcoded), and posts normalized rows back. The same image/code runs on a
client PC over a mobile IP (no proxy) or inside Docker behind a proxy.

The worker never decides stock or applies seller filters — it returns raw,
normalized fields and the backend does all filtering.

## Configuration (environment variables)

| Variable                  | Default                 | Description                                      |
| ------------------------- | ----------------------- | ------------------------------------------------ |
| `BACKEND_URL`             | `http://localhost:8000` | Base URL of the backend API.                     |
| `API_TOKEN`               | _(empty)_               | Sent as `X-API-Token` on every request.          |
| `WORKER_ID`               | `worker-1`              | Identifies this worker when claiming jobs.       |
| `PROXY_URL`               | _(unset)_               | Optional proxy, e.g. `http://user:pass@host:port`. Leave unset on a mobile IP. |
| `HEADLESS`                | `true`                  | Default headless mode (jobs may override).       |
| `POLL_INTERVAL_SECONDS`   | `5`                     | Sleep between polls when there is no work.        |
| `LOG_LEVEL`               | `INFO`                  | `DEBUG`/`INFO`/`WARNING`/`ERROR`.                |
| `MAX_REQUESTS_PER_MINUTE` | `10`                    | Token-bucket rate limit for page navigations.    |

## Run locally on a client PC (mobile IP, no proxy)

Run from the repo root (`Amazon Scraper/`) so the `worker.worker` package is on the path.

```bash
# 1. install dependencies (Python 3.11+ recommended)
pip install -r worker/requirements.txt

# 2. install the real Chrome channel used by the stealth context
playwright install chrome

# 3. point at the backend and start the worker (do NOT set PROXY_URL here)
#    PowerShell:
$env:BACKEND_URL = "http://localhost:8000"
$env:API_TOKEN   = "your-token"
$env:WORKER_ID   = "client-pc-1"
python -m worker.worker.main
```

```bash
#    bash/zsh equivalent:
export BACKEND_URL=http://localhost:8000
export API_TOKEN=your-token
export WORKER_ID=client-pc-1
python -m worker.worker.main
```

The scraper launches Chrome via Playwright's `chrome` channel, so `playwright
install chrome` (not just chromium) is required for local runs.

## Run in Docker with a proxy

The Docker image is based on the official Playwright Python image and uses
bundled Chromium.

```bash
# build (run from the worker/ directory)
docker build -t scraper-worker .

# run behind a proxy
docker run --rm \
  -e BACKEND_URL="http://host.docker.internal:8000" \
  -e API_TOKEN="your-token" \
  -e WORKER_ID="docker-worker-1" \
  -e PROXY_URL="http://user:pass@proxy-host:port" \
  scraper-worker
```

## Backend contract

- `POST {BACKEND_URL}/api/jobs/claim` with `{"worker_id": "<WORKER_ID>"}`
  - `204` → no work; the worker sleeps `POLL_INTERVAL_SECONDS` and retries.
  - `200` → `{"id", "group_id", "run_id", "kind": "pdp"|"serp", "payload": {...}}`.
- `POST {BACKEND_URL}/api/jobs/{id}/result` with
  `{"rows": [...], "metrics": {"net_kb", "items_ok", "items_skipped", "blocked_heavy"}, "captcha": bool, "error": string|null}`.

All requests carry the `X-API-Token` header.

## Output rows

- **PDP**: `{"asin", "title", "price", "shipping_text", "availability_text", "image_url", "product_url", "merchant_blob", "explicit_oos"}`.
  Pages that fail to load/parse emit `{"asin", "_skip_update": true, "skip_reason"}` so failures never flip backend state.
- **SERP**: `{"asin", "title", "price", "price_text", "image_url", "product_url", "seller_text", "shipping_text", "availability_text"}`.

## Captcha & network handling

- Captcha is detected when the page title contains `robot check` or the
  `captcha_form` selector matches. The job stops and returns the rows collected
  so far with `"captcha": true`.
- Network failures (`net::err_`, `ERR_NETWORK_*`, …) return `"error": "network: <detail>"`.
- All other exceptions are caught; the worker posts a result with `"error"` set
  and the poll loop keeps running.

## Package layout

```
worker/
  requirements.txt
  Dockerfile
  README.md
  worker/
    __init__.py
    config.py          # env-driven Config
    logging_setup.py   # structured JSON logging (matches backend)
    util.py            # selector access + parsing/normalization helpers
    api_client.py      # claim_job / submit_result
    browser.py         # stealth context, rate limiter, resource blocking, proxy
    result.py          # ScrapeResult dataclass
    pdp.py             # selector-driven async PDP scrape
    serp.py            # selector-driven sync SERP scrape
    main.py            # poll loop
```
