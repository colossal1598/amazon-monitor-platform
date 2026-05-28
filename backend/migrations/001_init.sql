-- Initial schema for the n8n Scraper Platform.
-- "group" is a reserved word in Postgres, so the table is named scrape_group.

CREATE TABLE IF NOT EXISTS selector_profile (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    marketplace     TEXT NOT NULL DEFAULT 'amazon.com',
    locale          TEXT NOT NULL DEFAULT 'en-IL',
    version         INTEGER NOT NULL DEFAULT 1,
    selectors       JSONB NOT NULL,
    is_default      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scrape_group (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('pdp', 'serp')),
    niche               TEXT,
    cadence             TEXT NOT NULL DEFAULT 'short' CHECK (cadence IN ('short', 'long')),
    interval_minutes    INTEGER,
    enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    selector_profile_id INTEGER REFERENCES selector_profile(id) ON DELETE SET NULL,
    headless            BOOLEAN NOT NULL DEFAULT TRUE,
    max_concurrent      INTEGER NOT NULL DEFAULT 2,
    notify_channel      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_run_at         TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS group_filter (
    group_id                INTEGER PRIMARY KEY REFERENCES scrape_group(id) ON DELETE CASCADE,
    accepted_sellers        JSONB NOT NULL DEFAULT '[]',
    required_keywords       JSONB NOT NULL DEFAULT '[]',
    blacklist_keywords      JSONB NOT NULL DEFAULT '[]',
    blacklist_asins         JSONB NOT NULL DEFAULT '[]',
    min_price               NUMERIC,
    max_price               NUMERIC,
    require_free_shipping    BOOLEAN NOT NULL DEFAULT FALSE,
    require_shipping_signal  BOOLEAN NOT NULL DEFAULT FALSE,
    require_shippable        BOOLEAN NOT NULL DEFAULT TRUE,
    price_drop_percent       NUMERIC NOT NULL DEFAULT 10,
    alert_new                BOOLEAN NOT NULL DEFAULT TRUE,
    alert_back_in_stock      BOOLEAN NOT NULL DEFAULT TRUE,
    alert_price_drop         BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS pdp_target (
    id          SERIAL PRIMARY KEY,
    group_id    INTEGER NOT NULL REFERENCES scrape_group(id) ON DELETE CASCADE,
    asin        TEXT NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, asin)
);

CREATE TABLE IF NOT EXISTS serp_target (
    id          SERIAL PRIMARY KEY,
    group_id    INTEGER NOT NULL REFERENCES scrape_group(id) ON DELETE CASCADE,
    search_url  TEXT NOT NULL,
    label       TEXT,
    scrape_mode TEXT NOT NULL DEFAULT 'newest_front' CHECK (scrape_mode IN ('newest_front', 'featured_full')),
    max_pages   INTEGER NOT NULL DEFAULT 1,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product_state (
    group_id          INTEGER NOT NULL REFERENCES scrape_group(id) ON DELETE CASCADE,
    asin              TEXT NOT NULL,
    title             TEXT,
    seller            TEXT,
    price             NUMERIC,
    in_stock          BOOLEAN NOT NULL DEFAULT FALSE,
    image_url         TEXT,
    product_url       TEXT,
    first_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, asin)
);

CREATE TABLE IF NOT EXISTS price_history (
    id          BIGSERIAL PRIMARY KEY,
    group_id    INTEGER NOT NULL REFERENCES scrape_group(id) ON DELETE CASCADE,
    asin        TEXT NOT NULL,
    price       NUMERIC,
    in_stock    BOOLEAN,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_price_history_group_asin ON price_history (group_id, asin, observed_at DESC);

CREATE TABLE IF NOT EXISTS alert (
    id          BIGSERIAL PRIMARY KEY,
    group_id    INTEGER REFERENCES scrape_group(id) ON DELETE SET NULL,
    asin        TEXT,
    alert_type  TEXT NOT NULL,
    title       TEXT,
    old_price   NUMERIC,
    new_price   NUMERIC,
    image_url   TEXT,
    product_url TEXT,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_alert_status ON alert (status, created_at);

CREATE TABLE IF NOT EXISTS run (
    id          BIGSERIAL PRIMARY KEY,
    group_id    INTEGER REFERENCES scrape_group(id) ON DELETE SET NULL,
    status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'error')),
    trigger     TEXT,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_group ON run (group_id, started_at DESC);

CREATE TABLE IF NOT EXISTS run_metric (
    run_id          BIGINT PRIMARY KEY REFERENCES run(id) ON DELETE CASCADE,
    duration_sec    NUMERIC,
    net_kb          NUMERIC,
    items_scraped   INTEGER,
    items_ok        INTEGER,
    items_skipped   INTEGER,
    captcha         INTEGER NOT NULL DEFAULT 0,
    alerts_emitted  INTEGER NOT NULL DEFAULT 0,
    blocked_heavy   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job (
    id                BIGSERIAL PRIMARY KEY,
    group_id          INTEGER NOT NULL REFERENCES scrape_group(id) ON DELETE CASCADE,
    run_id            BIGINT REFERENCES run(id) ON DELETE CASCADE,
    kind              TEXT NOT NULL CHECK (kind IN ('pdp', 'serp')),
    payload           JSONB NOT NULL,
    status            TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'claimed', 'done', 'failed')),
    claimed_by        TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    result            JSONB,
    error             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    lease_expires_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_job_status ON job (status, created_at);

CREATE TABLE IF NOT EXISTS app_setting (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
