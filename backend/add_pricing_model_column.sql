-- Migration script to add pricing model and subscription price columns to agents table
-- Run this if you have an existing database

-- Add pricing_model enum type
DO $$ BEGIN
    CREATE TYPE pricingmodel AS ENUM ('pay_per_use', 'monthly', 'quarterly');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Add pricing_model column (without default first)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS pricing_model pricingmodel;

-- Set default value using explicit cast
ALTER TABLE agents ALTER COLUMN pricing_model SET DEFAULT 'pay_per_use'::pricingmodel;

-- Update existing rows
UPDATE agents SET pricing_model = 'pay_per_use'::pricingmodel WHERE pricing_model IS NULL;

-- Add monthly_price column
ALTER TABLE agents ADD COLUMN IF NOT EXISTS monthly_price FLOAT;

-- Add quarterly_price column
ALTER TABLE agents ADD COLUMN IF NOT EXISTS quarterly_price FLOAT;
