-- Migration 021: Add timezone and structured schedule fields to job_schedules.
-- Enables user-friendly scheduling without exposing cron expressions.

ALTER TABLE job_schedules ADD COLUMN IF NOT EXISTS timezone VARCHAR NOT NULL DEFAULT 'UTC';
ALTER TABLE job_schedules ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ;
ALTER TABLE job_schedules ADD COLUMN IF NOT EXISTS days_of_week VARCHAR;
ALTER TABLE job_schedules ADD COLUMN IF NOT EXISTS schedule_time VARCHAR;
