-- Add a2a_enabled column to agents table for A2A (Agent-to-Agent) protocol support.
-- When true, the platform invokes the agent via A2A JSON-RPC (SendMessage) instead of raw HTTP POST.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS a2a_enabled BOOLEAN NOT NULL DEFAULT false;
