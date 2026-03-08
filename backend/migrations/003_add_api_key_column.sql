-- Add api_key column to agents table.

ALTER TABLE agents ADD COLUMN IF NOT EXISTS api_key VARCHAR;
