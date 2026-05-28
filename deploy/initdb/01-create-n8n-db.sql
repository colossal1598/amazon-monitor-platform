-- Create a dedicated database for n8n on first Postgres init.
-- Runs only when the pgdata volume is empty.
SELECT 'CREATE DATABASE n8n'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'n8n')\gexec
