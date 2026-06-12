# n8n Data Tables

Group configuration, scrape targets, filters, and browser/selector profiles live in n8n **Data Tables**. Workflows read these tables to enqueue jobs and to filter/diff scrape results.

Create tables in n8n: **Overview → Data tables → Create Data table**. Import seed rows manually (copy/paste from `seed/*.json`) or via the Data Table **Insert** node.

> **Selectors source of truth:** `backend/seed/default_selector_profile.json`. The seed file `seed/selector_profiles.json` embeds the same selector JSON for the `amazon-us-default` profile. When Amazon changes markup, update that backend file first, then refresh the Data Table row.

---

## Table: `groups`

One row per monitor group. `group_key` is the stable identifier used in all backend API calls.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `group_key` | string | yes | Unique slug, e.g. `demo-serp` |
| `name` | string | yes | Human-readable label |
| `kind` | string | yes | `pdp` or `serp` |
| `enabled` | boolean | yes | `false` skips scheduling |
| `interval_minutes` | number | yes | Minutes between scheduled runs |
| `last_run_at` | string | no | ISO timestamp; updated by `scheduler.json` |
| `selector_profile_key` | string | yes | FK → `selector_profiles.profile_key` |
| `browser_profile_key` | string | yes | FK → `browser_profiles.profile_key` (`fast` for normal runs) |
| `notify_channel` | string | no | WhatsApp JID for this group's alerts |
| `headless` | boolean | no | Default `true` |
| `max_concurrent` | number | no | PDP parallel tabs (default `2`) |

---

## Table: `serp_targets`

Search URLs for `kind=serp` groups.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `group_key` | string | yes | Matches `groups.group_key` |
| `label` | string | no | Notes / display name |
| `search_url` | string | yes | Full Amazon search URL |
| `scrape_mode` | string | no | `newest_front` or `featured_full` |
| `max_pages` | number | no | Pages to scrape (default `1`) |
| `enabled` | boolean | yes | `false` skips this target |

---

## Table: `pdp_targets`

ASINs for `kind=pdp` groups.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `group_key` | string | yes | Matches `groups.group_key` |
| `asin` | string | yes | Amazon ASIN |
| `enabled` | boolean | yes | `false` skips this target |
| `notes` | string | no | Optional notes |

---

## Table: `group_filters`

Per-group filter and alert thresholds (port of backend `GroupFilterModel`).

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `group_key` | string | yes | Matches `groups.group_key` |
| `accepted_sellers` | string | no | JSON array of seller substrings |
| `required_keywords` | string | no | JSON array; title/seller must contain all |
| `blacklist_keywords` | string | no | JSON array; reject if any match |
| `blacklist_asins` | string | no | JSON array of ASINs |
| `min_price` | number | no | Minimum price (inclusive) |
| `max_price` | number | no | Maximum price (inclusive) |
| `require_free_shipping` | boolean | no | Require “free” in shipping text |
| `require_shipping_signal` | boolean | no | Require shipping/delivery signal |
| `require_shippable` | boolean | no | Default `true` |
| `price_drop_percent` | number | no | Alert threshold % (default `10`) |
| `alert_new` | boolean | no | Emit `new_product` alerts |
| `alert_back_in_stock` | boolean | no | Emit `back_in_stock` alerts |
| `alert_price_drop` | boolean | no | Emit `price_drop` alerts |

---

## Table: `selector_profiles`

Versioned selector bundles passed to the worker in each job's `payload.selectors`.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `profile_key` | string | yes | Unique key, e.g. `amazon-us-default` |
| `name` | string | yes | Display name |
| `marketplace` | string | no | e.g. `amazon.com` |
| `locale` | string | no | e.g. `en-IL` |
| `is_default` | boolean | no | Mark default profile |
| `selectors_json` | string | yes | **Stringified** JSON object `{ nav, pdp, serp }` — copy from `backend/seed/default_selector_profile.json` → `selectors` field |

---

## Table: `browser_profiles`

Browser/navigation presets merged into `payload.browser`.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `profile_key` | string | yes | `fast`, `retry`, or `recovery` |
| `block_heavy` | boolean | no | Block images/fonts/media |
| `headless` | boolean | no | Headless Chrome |
| `channel` | string | no | Playwright channel (`chrome`) |
| `goto_timeout_ms` | number | no | Navigation timeout |
| `ready_wait_ms` | number | no | Selector wait timeout |
| `max_goto_retries` | number | no | Navigation retries |
| `wait_until` | string | no | e.g. `commit` |

### Seed: `browser_profiles`

Import `seed/browser_profiles.json`:

| profile_key | block_heavy | goto_timeout_ms | ready_wait_ms | max_goto_retries |
|-------------|-------------|-----------------|---------------|------------------|
| `fast` | true | 12000 | 8000 | 1 |
| `retry` | true | 20000 | 15000 | 2 |
| `recovery` | false | 30000 | 20000 | 3 |

`recovery` is used by `recovery.json` when `process_result.json` detects poor `scrape_quality` and `attempt < 3`.

---

## Demo seed

`seed/demo_group.json` contains one enabled SERP group (`demo-serp`), a search target, and default filters. Import into `groups`, `serp_targets`, and `group_filters` after creating the profile/browser rows.

---

## Checklist

- [ ] Tables created: `groups`, `serp_targets`, `pdp_targets`, `group_filters`, `selector_profiles`, `browser_profiles`
- [ ] `browser_profiles` seeded (`fast`, `retry`, `recovery`)
- [ ] `selector_profiles` row with `selectors_json` from `backend/seed/default_selector_profile.json`
- [ ] At least one enabled `groups` row with matching targets and `group_filters`
- [ ] Workflows imported; Data Table nodes re-linked **From list** if names differ
- [ ] `N8N_JOB_DONE_WEBHOOK_URL` points at `process_result.json` webhook production URL

See **[n8n/README.md](../README.md)** for workflow import and env vars.
