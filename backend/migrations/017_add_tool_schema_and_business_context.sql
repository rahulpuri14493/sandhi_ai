-- Add schema metadata and optional business description for SQL tools (Postgres/MySQL).
-- schema_metadata: JSON from introspection (tables, columns, PK, FK); read-only, no credentials.
-- business_description: optional short context for the agent (e.g. "Sales DB: orders, customers").
-- Idempotent: duplicate_column is ignored.

DO $$ BEGIN ALTER TABLE mcp_tool_configs ADD COLUMN schema_metadata TEXT; EXCEPTION WHEN duplicate_column THEN null; END $$;
DO $$ BEGIN ALTER TABLE mcp_tool_configs ADD COLUMN business_description VARCHAR(2000); EXCEPTION WHEN duplicate_column THEN null; END $$;
