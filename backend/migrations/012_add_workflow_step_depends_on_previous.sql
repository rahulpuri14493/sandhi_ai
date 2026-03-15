-- Support independent vs sequential agent workflows (BRD-driven).
-- When false, the step does not receive previous agent output (agents work independently).
ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS depends_on_previous BOOLEAN NOT NULL DEFAULT true;
