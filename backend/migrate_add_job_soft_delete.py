"""
Migration: Add soft delete support to jobs table
Run this script to add the deleted_at column for soft delete functionality
"""
from db.database import engine
from sqlalchemy import text

def upgrade():
    """Add deleted_at column and index to jobs table"""
    with engine.connect() as conn:
        # Add deleted_at column
        conn.execute(text("""
            ALTER TABLE jobs ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;
        """))
        
        # Add index for better query performance
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_jobs_deleted_at ON jobs(deleted_at);
        """))
        
        conn.commit()
    
    print("✅ Migration complete: Added soft delete to jobs table")
    print("   - Added 'deleted_at' TIMESTAMP column")
    print("   - Added index on 'deleted_at' for performance")

def downgrade():
    """Remove deleted_at column and index from jobs table"""
    with engine.connect() as conn:
        conn.execute(text("""
            DROP INDEX IF EXISTS idx_jobs_deleted_at;
            ALTER TABLE jobs DROP COLUMN IF EXISTS deleted_at;
        """))
        conn.commit()
    
    print("✅ Migration rollback complete")

if __name__ == "__main__":
    try:
        upgrade()
    except Exception as e:
        print(f"❌ Migration failed: {str(e)}")
        raise
