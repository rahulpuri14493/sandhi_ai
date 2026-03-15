-- Migration 020: Create job_schedules table (if not already created by ORM)
-- and add is_one_time column for one-time vs recurring schedule support.

CREATE TABLE IF NOT EXISTS job_schedules (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    cron_expression VARCHAR NOT NULL,
    status schedulestatus NOT NULL DEFAULT 'active',
    last_run_time TIMESTAMP,
    next_run_time TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_job_schedules_id ON job_schedules (id);

-- Add is_one_time column (one-time schedules auto-deactivate after first execution)
ALTER TABLE job_schedules ADD COLUMN IF NOT EXISTS is_one_time BOOLEAN NOT NULL DEFAULT FALSE;
