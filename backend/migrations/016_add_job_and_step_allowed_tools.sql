-- Job-level: which tools/connections are in scope for this job (empty/null = all business tools)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS allowed_platform_tool_ids TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS allowed_connection_ids TEXT;

-- Step-level: which tools this step (agent) can use (empty/null = all job-level tools)
ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS allowed_platform_tool_ids TEXT;
ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS allowed_connection_ids TEXT;
