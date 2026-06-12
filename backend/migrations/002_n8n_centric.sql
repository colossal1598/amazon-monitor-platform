-- n8n-centric schema: group_key identifiers, thin job queue, webhook processing flags.

ALTER TABLE job ADD COLUMN IF NOT EXISTS group_key TEXT;
ALTER TABLE job ADD COLUMN IF NOT EXISTS browser_profile TEXT;
ALTER TABLE job ADD COLUMN IF NOT EXISTS scrape_quality TEXT;
ALTER TABLE job ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1;
ALTER TABLE job ADD COLUMN IF NOT EXISTS n8n_processed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE job ALTER COLUMN group_id DROP NOT NULL;

ALTER TABLE alert ADD COLUMN IF NOT EXISTS group_key TEXT;
ALTER TABLE alert ADD COLUMN IF NOT EXISTS notify_channel TEXT;

ALTER TABLE product_state ADD COLUMN IF NOT EXISTS group_key TEXT;
ALTER TABLE product_state ALTER COLUMN group_id DROP NOT NULL;

ALTER TABLE run ADD COLUMN IF NOT EXISTS group_key TEXT;

CREATE INDEX IF NOT EXISTS idx_job_done_unprocessed ON job (status, n8n_processed)
    WHERE status = 'done' AND n8n_processed = FALSE;
