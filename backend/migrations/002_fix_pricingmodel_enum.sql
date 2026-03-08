-- Fix pricingmodel enum if it was created incorrectly (run only if 001 caused issues).

DO $$ BEGIN
    DROP TYPE IF EXISTS pricingmodel CASCADE;
    CREATE TYPE pricingmodel AS ENUM ('pay_per_use', 'monthly', 'quarterly');
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Error creating enum: %', SQLERRM;
END $$;

ALTER TABLE agents ADD COLUMN IF NOT EXISTS pricing_model pricingmodel DEFAULT 'pay_per_use'::pricingmodel;
UPDATE agents SET pricing_model = 'pay_per_use'::pricingmodel WHERE pricing_model IS NULL;
