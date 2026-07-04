-- Switch product_state PK from (group_id, asin) to (group_key, asin).
-- The n8n-centric flow uses group_key (not group_id) as the partition key.
-- Old PK prevented INSERT when group_id is NULL.

-- Backfill any rows missing group_key before adding NOT NULL.
UPDATE product_state SET group_key = 'unknown' WHERE group_key IS NULL;
ALTER TABLE product_state ALTER COLUMN group_key SET NOT NULL;

ALTER TABLE product_state DROP CONSTRAINT IF EXISTS product_state_pkey;
ALTER TABLE product_state ADD CONSTRAINT product_state_pkey PRIMARY KEY (group_key, asin);

CREATE INDEX IF NOT EXISTS idx_product_state_group_id ON product_state (group_id, asin);
