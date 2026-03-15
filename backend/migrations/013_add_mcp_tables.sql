-- MCP (Model Context Protocol): server connections and platform tool configs per user.
-- Credentials/config are stored encrypted in application layer; DB holds ciphertext only.
-- Requires: users table.

DO $$ BEGIN
    CREATE TYPE mcptooltype AS ENUM ('vector_db', 'postgres', 'filesystem');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS mcp_server_connections (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    base_url VARCHAR(2048) NOT NULL,
    endpoint_path VARCHAR(255) NOT NULL DEFAULT '/mcp',
    auth_type VARCHAR(32) NOT NULL DEFAULT 'none',
    encrypted_credentials TEXT,
    is_platform_configured BOOLEAN NOT NULL DEFAULT false,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mcp_tool_configs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tool_type mcptooltype NOT NULL,
    name VARCHAR(255) NOT NULL,
    encrypted_config TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mcp_server_connections_user_id ON mcp_server_connections(user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_tool_configs_user_id ON mcp_tool_configs(user_id);
