-- Migration script to add api_key column to agents table
-- Run this if you have an existing database

ALTER TABLE agents ADD COLUMN IF NOT EXISTS api_key VARCHAR;
