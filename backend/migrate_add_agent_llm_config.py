#!/usr/bin/env python3
"""
Migration script to add llm_model and temperature columns to agents table.
"""
import sys
from sqlalchemy import create_engine, text
from db.database import DATABASE_URL


def migrate():
    engine = create_engine(DATABASE_URL)

    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_model VARCHAR;"))
            conn.execute(text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS temperature DOUBLE PRECISION;"))
            conn.commit()
            print("✓ Successfully added 'llm_model' and 'temperature' columns to agents table")
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    migrate()

