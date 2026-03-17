#!/usr/bin/env python3
"""
Migration script to add files column to jobs table
"""

import sys
from sqlalchemy import create_engine, text
from db.database import DATABASE_URL


def migrate():
    """Add files column to jobs table"""
    engine = create_engine(DATABASE_URL)

    try:
        with engine.connect() as conn:
            # Add files column if it doesn't exist
            conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS files TEXT;"))
            conn.commit()
            print("✓ Successfully added 'files' column to jobs table")

            # Also create job_questions table if it doesn't exist
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS job_questions (
                    id SERIAL PRIMARY KEY,
                    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    question TEXT NOT NULL,
                    answer TEXT,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    answered_at TIMESTAMP
                );
            """))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_job_questions_job_id ON job_questions(job_id);"
                )
            )
            conn.commit()
            print("✓ Successfully created 'job_questions' table")

    except Exception as e:
        print(f"✗ Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    migrate()
