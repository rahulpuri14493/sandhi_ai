-- Fix script for pricingmodel enum issue
-- This script checks and fixes the enum values

-- First, check what enum values exist
-- SELECT enumlabel FROM pg_enum WHERE enumtypid = (SELECT oid FROM pg_type WHERE typname = 'pricingmodel') ORDER BY enumsortorder;

-- If the enum doesn't have the correct values, we need to drop and recreate it
-- WARNING: This will require dropping the column first, then recreating it

-- Step 1: Drop the column (this will lose data, so backup first!)
-- ALTER TABLE agents DROP COLUMN IF EXISTS pricing_model;

-- Step 2: Drop the enum type
-- DROP TYPE IF EXISTS pricingmodel;

-- Step 3: Recreate the enum with correct values
DO $$ BEGIN
    DROP TYPE IF EXISTS pricingmodel CASCADE;
    CREATE TYPE pricingmodel AS ENUM ('pay_per_use', 'monthly', 'quarterly');
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Error creating enum: %', SQLERRM;
END $$;

-- Step 4: Re-add the column
ALTER TABLE agents ADD COLUMN IF NOT EXISTS pricing_model pricingmodel DEFAULT 'pay_per_use'::pricingmodel;

-- Step 5: Update existing NULL values
UPDATE agents SET pricing_model = 'pay_per_use'::pricingmodel WHERE pricing_model IS NULL;
