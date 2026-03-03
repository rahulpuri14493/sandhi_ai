#!/usr/bin/env python3
"""
Migration script to add pricing model and subscription price columns to agents table
Run this script to update your database schema
"""
import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agent_marketplace")

def run_migration():
    """Run the migration to add pricing model columns"""
    try:
        # Parse DATABASE_URL
        # Format: postgresql://user:password@host:port/database
        import re
        match = re.match(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', DATABASE_URL)
        if not match:
            print("Error: Invalid DATABASE_URL format")
            sys.exit(1)
        
        user, password, host, port, database = match.groups()
        
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database
        )
        # Use autocommit for DDL operations
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        print("Connected to database. Running migration...")
        
        # Create pricingmodel enum type if it doesn't exist
        print("Creating pricingmodel enum type...")
        cursor.execute("""
            DO $$ BEGIN
                CREATE TYPE pricingmodel AS ENUM ('pay_per_use', 'monthly', 'quarterly');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
        """)
        
        # Add pricing_model column (without default first)
        print("Adding pricing_model column...")
        cursor.execute("""
            DO $$ BEGIN
                ALTER TABLE agents ADD COLUMN pricing_model pricingmodel;
            EXCEPTION
                WHEN duplicate_column THEN null;
            END $$;
        """)
        
        # Set default value for the column using explicit cast
        print("Setting default value...")
        try:
            cursor.execute("""
                ALTER TABLE agents ALTER COLUMN pricing_model SET DEFAULT 'pay_per_use'::pricingmodel;
            """)
        except psycopg2.Error as e:
            # If default already set or column doesn't exist, that's fine
            if "does not exist" not in str(e) and "already" not in str(e).lower():
                print(f"Warning setting default: {e}")
        
        # Update existing rows that have NULL
        print("Updating existing rows...")
        try:
            cursor.execute("""
                UPDATE agents SET pricing_model = 'pay_per_use'::pricingmodel WHERE pricing_model IS NULL;
            """)
        except psycopg2.Error as e:
            print(f"Warning updating rows: {e}")
        
        # Add monthly_price column
        print("Adding monthly_price column...")
        cursor.execute("""
            ALTER TABLE agents ADD COLUMN IF NOT EXISTS monthly_price FLOAT;
        """)
        
        # Add quarterly_price column
        print("Adding quarterly_price column...")
        cursor.execute("""
            ALTER TABLE agents ADD COLUMN IF NOT EXISTS quarterly_price FLOAT;
        """)
        
        cursor.close()
        conn.close()
        
        print("✓ Migration completed successfully!")
        print("Added columns: pricing_model, monthly_price, quarterly_price")
        
    except psycopg2.Error as e:
        print(f"Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_migration()
