-- Migration script to add files column to jobs table
-- Run this if you have an existing database

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS files TEXT;
