-- Add pricing model and subscription price columns to agents table.
-- Requires: agents table (from app create_all or initial setup).
-- Drop/recreate enum so we always have correct values (fixes existing broken enum from older runs).

DROP TYPE IF EXISTS pricingmodel CASCADE;

DO $$ BEGIN
    CREATE TYPE pricingmodel AS ENUM ('pay_per_use', 'monthly', 'quarterly');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

ALTER TABLE agents ADD COLUMN IF NOT EXISTS pricing_model pricingmodel;
ALTER TABLE agents ALTER COLUMN pricing_model SET DEFAULT 'pay_per_use'::pricingmodel;
UPDATE agents SET pricing_model = 'pay_per_use'::pricingmodel WHERE pricing_model IS NULL;

ALTER TABLE agents ADD COLUMN IF NOT EXISTS monthly_price FLOAT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS quarterly_price FLOAT;
