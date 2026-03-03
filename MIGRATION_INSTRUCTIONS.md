# Migration Instructions: Add Pricing Model Columns

The database needs to be updated to include the new pricing model columns. Choose one of the following methods:

## Method 1: Using Docker Compose (Recommended)

If you're using Docker Compose for your database:

```bash
cd /Users/rahulpuri/Desktop/me/idea
docker-compose exec db psql -U postgres -d agent_marketplace -f /tmp/migration.sql
```

Or run the SQL directly:

```bash
docker-compose exec db psql -U postgres -d agent_marketplace << 'EOF'
-- Create pricingmodel enum type
DO $$ BEGIN
    CREATE TYPE pricingmodel AS ENUM ('pay_per_use', 'monthly', 'quarterly');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Add pricing_model column
ALTER TABLE agents ADD COLUMN IF NOT EXISTS pricing_model pricingmodel DEFAULT 'pay_per_use';

-- Add monthly_price column
ALTER TABLE agents ADD COLUMN IF NOT EXISTS monthly_price FLOAT;

-- Add quarterly_price column
ALTER TABLE agents ADD COLUMN IF NOT EXISTS quarterly_price FLOAT;

-- Update existing rows
UPDATE agents SET pricing_model = 'pay_per_use' WHERE pricing_model IS NULL;
EOF
```

## Method 2: Using Local PostgreSQL

If you're using a local PostgreSQL instance:

```bash
psql -U postgres -d agent_marketplace -f backend/add_pricing_model_column.sql
```

Or run the SQL directly:

```bash
psql -U postgres -d agent_marketplace << 'EOF'
-- Create pricingmodel enum type
DO $$ BEGIN
    CREATE TYPE pricingmodel AS ENUM ('pay_per_use', 'monthly', 'quarterly');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Add pricing_model column
ALTER TABLE agents ADD COLUMN IF NOT EXISTS pricing_model pricingmodel DEFAULT 'pay_per_use';

-- Add monthly_price column
ALTER TABLE agents ADD COLUMN IF NOT EXISTS monthly_price FLOAT;

-- Add quarterly_price column
ALTER TABLE agents ADD COLUMN IF NOT EXISTS quarterly_price FLOAT;

-- Update existing rows
UPDATE agents SET pricing_model = 'pay_per_use' WHERE pricing_model IS NULL;
EOF
```

## Method 3: Using Python Script

If you have Python and psycopg2 installed:

```bash
cd /Users/rahulpuri/Desktop/me/idea/backend
python3 run_pricing_migration.py
```

Or if using a virtual environment:

```bash
cd /Users/rahulpuri/Desktop/me/idea/backend
source venv/bin/activate  # On Windows: venv\Scripts\activate
python run_pricing_migration.py
```

## Verification

After running the migration, verify the columns exist:

**Using Docker:**
```bash
docker-compose exec db psql -U postgres -d agent_marketplace -c "\d agents"
```

**Using Local PostgreSQL:**
```bash
psql -U postgres -d agent_marketplace -c "\d agents"
```

You should see `pricing_model`, `monthly_price`, and `quarterly_price` in the table columns.

## Troubleshooting

### Error: "relation agents does not exist"
The agents table hasn't been created yet. You need to create the tables first by running the backend application once, or manually create them.

### Error: "type pricingmodel already exists"
This is fine! The migration uses `IF NOT EXISTS` clauses, so it's safe to run multiple times.

### Error: "column pricing_model already exists"
The migration has already been applied. You can proceed to use the application.
