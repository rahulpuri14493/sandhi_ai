-- Tool visibility: control what tool information agents receive (no credentials are ever sent).
-- full = full descriptors (name, description, schema); names_only = names/short desc only; none = no tools.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tool_visibility VARCHAR(20);
-- Step-level override (null = use job's tool_visibility)
ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS tool_visibility VARCHAR(20);
