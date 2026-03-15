-- Add endpoint_path to mcp_server_connections if missing (e.g. table created before 013 had this column).
ALTER TABLE mcp_server_connections ADD COLUMN IF NOT EXISTS endpoint_path VARCHAR(255) NOT NULL DEFAULT '/mcp';
