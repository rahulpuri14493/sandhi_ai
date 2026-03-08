-- Add files column to jobs table.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS files TEXT;
