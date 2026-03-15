-- Add failure_reason column to jobs table.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS failure_reason TEXT;
