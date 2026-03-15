# Steps to Add `api_key` Column to Agents Table

## Method 1: Using Docker Compose (Recommended)

If you're using Docker Compose for your database:

### Step 1: Make sure your database is running
```bash
cd /Users/rahulpuri/Desktop/me/idea
docker-compose up -d db
```

### Step 2: Connect to the PostgreSQL container and run the migration
```bash
docker-compose exec db psql -U postgres -d agent_marketplace -c "ALTER TABLE agents ADD COLUMN IF NOT EXISTS api_key VARCHAR;"
```

**OR** if you prefer to use the SQL file:

```bash
docker-compose exec -T db psql -U postgres -d agent_marketplace < backend/migrations/003_add_api_key_column.sql
```

### Step 3: Verify the column was added
```bash
docker-compose exec db psql -U postgres -d agent_marketplace -c "\d agents"
```

You should see `api_key` listed in the table columns.

---

## Method 2: Using Local PostgreSQL

If you're using a local PostgreSQL instance (not Docker):

### Step 1: Make sure PostgreSQL is running
```bash
# Check if PostgreSQL is running (macOS)
brew services list | grep postgresql
```

### Step 2: Run the migration SQL
```bash
psql -U postgres -d agent_marketplace -f backend/migrations/003_add_api_key_column.sql
```

**OR** run the SQL directly:
```bash
psql -U postgres -d agent_marketplace -c "ALTER TABLE agents ADD COLUMN IF NOT EXISTS api_key VARCHAR;"
```

### Step 3: Verify the column was added
```bash
psql -U postgres -d agent_marketplace -c "\d agents"
```

---

## Method 3: Using Python Script (Alternative)

If you prefer to run it programmatically:

### Step 1: Create and run a Python migration script
```bash
cd backend
python3 -c "
from db.database import engine
with engine.connect() as conn:
    conn.execute('ALTER TABLE agents ADD COLUMN IF NOT EXISTS api_key VARCHAR')
    conn.commit()
print('Migration completed successfully!')
"
```

---

## Verification

After running any of the methods above, verify the column exists:

**Using Docker:**
```bash
docker-compose exec db psql -U postgres -d agent_marketplace -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'agents' AND column_name = 'api_key';"
```

**Using Local PostgreSQL:**
```bash
psql -U postgres -d agent_marketplace -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'agents' AND column_name = 'api_key';"
```

You should see output showing `api_key` with type `character varying`.

---

## Troubleshooting

### Error: "relation agents does not exist"
This means the `agents` table hasn't been created yet. You need to create the tables first:

```bash
# Using Python
cd backend
python3 -c "
from db.database import Base
from models.agent import Agent
from models.user import User
from models.job import Job
from models.communication import AgentCommunication
from models.transaction import Transaction
from models.audit_log import AuditLog
Base.metadata.create_all(bind=engine)
print('Tables created!')
"
```

### Error: "column api_key already exists"
This is fine! The `IF NOT EXISTS` clause prevents errors if the column already exists. Your migration is already applied.

### Error: "password authentication failed"
Make sure you're using the correct database credentials. Check your `.env` file or `docker-compose.yml` for the correct username and password.
