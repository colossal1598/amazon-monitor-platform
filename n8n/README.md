# n8n Scraper Platform — Orchestration Workflows

n8n is the **orchestrator** for the Amazon scraper platform. It does three things only — all
business logic lives in the backend, so these workflows are intentionally thin:

- **(A) Trigger scrape runs on a schedule** — `orchestrator_short.json`, `orchestrator_long.json`
- **(B) Poll the backend for pending alerts and deliver them via the WhatsApp `wa-server`** — `notifier.json`
- **(C) Basic error / ops handling** — built into each workflow via HTTP retries, `Continue On Fail`,
  and an explicit failure branch in the notifier.

## Workflows

| File | Trigger | What it does |
| --- | --- | --- |
| `workflows/orchestrator_short.json` | Schedule, every **1 minute** | `POST {BACKEND_URL}/api/runs` with `{"cadence":"short","trigger":"scheduled"}` |
| `workflows/orchestrator_long.json` | Schedule, every **5 minutes** | `POST {BACKEND_URL}/api/runs` with `{"cadence":"long","trigger":"scheduled"}` |
| `workflows/notifier.json` | Schedule, every **1 minute** | `GET {BACKEND_URL}/api/alerts/pending` → build message → `POST {WA_API_URL}/send` → mark each alert `sent`/`failed` |

> The backend enforces each group's polling interval, so triggering the orchestrators every
> minute / five minutes is safe — only groups that are actually due are enqueued.

## Required environment variables

Set these as n8n **environment variables** (they are read in expressions via `{{ $env.NAME }}`).
For self-hosted n8n, export them in the process / container environment before starting n8n.

| Variable | Example | Used by | Purpose |
| --- | --- | --- | --- |
| `BACKEND_URL` | `http://backend:8000` | all | Base URL of the backend API (no trailing slash) |
| `API_TOKEN` | `secret-token` | all | Sent as the `X-API-Token` header on every backend call |
| `WA_API_URL` | `http://wa-server:3001` | notifier | Base URL of the WhatsApp `wa-server` (no trailing slash) |
| `WA_API_KEY` | `eTjW1zf2cDDZ` | notifier | Sent as the `x-api-key` header to `wa-server` |
| `WA_GROUP_ID` | `9725XXXXXXXX@c.us` | notifier | Fallback recipient JID when an alert has no `notify_channel` |

> n8n only exposes `$env` in expressions when `N8N_BLOCK_ENV_ACCESS_IN_NODE` is **not** set to
> `true` (the default). If your instance blocks env access, either unset that flag or replace the
> `{{ $env.* }}` expressions with n8n **Variables** (`{{ $vars.* }}`) or hard-coded values /
> credentials.

## API contracts (for reference)

Backend (`X-API-Token` header on all requests):

- `POST /api/runs` — body `{"cadence":"short"|"long","trigger":"scheduled"}` → `{"runs":[...],"due_count":int}`
- `GET /api/alerts/pending?limit=50` → array of alerts `{id, group_id, asin, alert_type, title, old_price, new_price, image_url, product_url, group_name, notify_channel}`
- `POST /api/alerts/{id}/sent` — mark delivered
- `POST /api/alerts/{id}/failed` — mark failed

WhatsApp `wa-server` (confirmed against `wa-server/server.js`):

- `POST {WA_API_URL}/send` — header `x-api-key`, JSON body `{"to":"<jid>","message":"<text>","image_url":"<optional http(s) url>"}`
- `to` must be a full WhatsApp JID (e.g. `9725XXXXXXXX@c.us`). The notifier uses the alert's
  `notify_channel` when present, otherwise `WA_GROUP_ID`.
- `image_url` is only used when it is a valid `http(s)` URL; otherwise the server sends a text-only message.

## Importing the workflows

1. Open n8n → top-right **⋯ / Create** menu → **Import from File** (or **Workflows → Import from File**).
2. Select a file from `n8n/workflows/` (`orchestrator_short.json`, `orchestrator_long.json`, `notifier.json`).
3. Repeat for each of the three files. They import as **inactive**.
4. Set the environment variables listed above and restart n8n if needed.

CLI alternative (self-hosted):

```bash
n8n import:workflow --separate --input=./n8n/workflows
```

## Activating

1. Open each imported workflow.
2. (Optional) Run once manually to confirm the backend / wa-server are reachable.
3. Toggle **Active** (top-right). The Schedule Triggers only fire while the workflow is active.

Activate all three for normal operation: both orchestrators (so short- and long-cadence groups
are enqueued) plus the notifier (so alerts get delivered).

## Error / ops handling

- Every backend/WhatsApp HTTP node has **retry on failure** enabled (`maxTries: 3`,
  `waitBetweenTries: 2000ms`).
- In `notifier.json`, **Send WhatsApp** uses `onError: continueErrorOutput` (the modern
  equivalent of "Continue On Fail" that exposes a second, error output):
  - success output → `POST /api/alerts/{id}/sent`
  - error output → `POST /api/alerts/{id}/failed`
- The orchestrator HTTP nodes use `onError: continueRegularOutput` so a transient backend hiccup
  does not stop the schedule; the next tick retries naturally.

## Alternative: webhook push instead of polling (note)

The notifier **polls** `GET /api/alerts/pending` every minute. If you prefer the backend to
**push** alerts to n8n the moment they are created, use a Webhook-trigger variant:

1. Build a workflow that starts with a **Webhook** node (`n8n-nodes-base.webhook`, method `POST`)
   instead of the Schedule Trigger. Everything downstream — Split Out → Prepare Alert →
   Send WhatsApp → Mark Sent/Failed — stays the same. The webhook should receive either a single
   alert object or an array of alerts (Split Out handles the array).
2. Copy the node's **Production URL**.
3. Configure the backend with env `N8N_ALERTS_WEBHOOK_URL` pointing at that URL; the backend then
   `POST`s new alerts to n8n directly. In push mode you typically disable the polling notifier to
   avoid double-sending.

## Assumptions about node parameters

These were chosen against current n8n (2025/2026) node definitions; adjust if your version differs:

- **HTTP Request** node `typeVersion 4.2`: JSON body via `specifyBody: "json"` + `jsonBody`
  (an expression returning an object). Retry/“Continue On Fail” are node-level properties
  (`retryOnFail`, `maxTries`, `waitBetweenTries`, `onError`), not parameters.
- **`onError`** values: `continueRegularOutput` (formerly "Continue On Fail") and
  `continueErrorOutput` (adds the second error output). Older n8n used a boolean `continueOnFail` —
  if your editor shows that instead, enable "Continue On Fail" on the HTTP nodes.
- **Get Pending Alerts** uses `options.response.response.fullResponse: true` so the response array
  lands in a single item under `body`, which **Split Out** (`fieldToSplitOut: "body"`) then expands
  into one item per alert. If your n8n version already returns a top-level array as multiple items,
  you can remove the `fullResponse` option and the Split Out node and reference fields directly.
- **Split Out** node type is `n8n-nodes-base.splitOut` (`typeVersion 1`). On older instances this
  capability lives in **Item Lists** (`n8n-nodes-base.itemLists`, operation *Split Out Items*).
- **Schedule Trigger** `typeVersion 1.2` interval form: `rule.interval[].field = "minutes"` with
  `minutesInterval`.
- **Set** node `typeVersion 3.4` uses the `assignments.assignments[]` structure.
- Downstream **Mark Sent / Mark Failed** reference the alert id via
  `{{ $('Prepare Alert').item.json.alert_id }}` because the Send WhatsApp response replaces the
  item JSON with the wa-server response.
