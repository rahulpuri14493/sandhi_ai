-- Add conversation column to jobs table.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS conversation TEXT;
