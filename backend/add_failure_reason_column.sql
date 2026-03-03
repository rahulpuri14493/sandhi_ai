-- Migration script to add failure_reason column to jobs table
-- Run this if you have an existing database

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS failure_reason TEXT;
