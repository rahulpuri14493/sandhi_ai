#!/usr/bin/env python3
"""
Migration script to add llm_model and temperature columns to agents table.
"""

import sys
from sqlalchemy import create_engine, text
from db.database import DATABASE_URL

def migrate_database(engine: create_engine) -> None:
    """
    Migrate the database by adding 'llm_model' and 'temperature' columns to the 'agents' table.

    Args:
        engine (create_engine): The database engine to use for the migration.
    """
    try:
        # Establish a connection to the database
        with engine.connect() as conn:
            # Add the 'llm_model' column if it does not already exist
            conn.execute(text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_model VARCHAR;"))
            # Add the 'temperature' column if it does not already exist
            conn.execute(text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS temperature DOUBLE PRECISION;"))
            # Commit the changes to the database
            conn.commit()
            print("✓ Successfully added 'llm_model' and 'temperature' columns to agents table")
    except sqlalchemy.exc.OperationalError as e:
        # Handle operational errors, such as database connection issues
        print(f"✗ Migration failed: {e}")
        sys.exit(1)
    except Exception as e:
        # Handle any other unexpected errors
        print(f"✗ Unexpected migration failure: {e}")
        sys.exit(1)


def main() -> None:
    """
    The main entry point for the migration script.
    """
    # Create the database engine from the database URL
    engine = create_engine(DATABASE_URL)
    migrate_database(engine)


if __name__ == "__main__":
    main()