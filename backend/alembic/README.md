# Alembic migrations

Schema changes are managed with **Alembic**. Migrations run **automatically on application startup** (PostgreSQL only). **No manual steps** are required for new or existing databases.

## Automatic behavior (zero manual intervention)

- **New database (empty):** On first startup, the app runs all migrations in order (001_initial, 002_indexes, …). Tables and indexes are created.
- **Existing database** (e.g. created with legacy SQL 001–019 or a previous deploy): On startup, the app detects that application tables already exist and that `alembic_version` is missing. It stamps the initial revision, then runs only **pending** migrations (e.g. 002_indexes, later revisions). The initial migration is not re-run, so no “table already exists” errors.
- **Already on Alembic:** If `alembic_version` is present, the app runs `alembic upgrade head` and applies only new revisions.

## Quick reference

- **Deploy:** Start the app. No `stamp` or manual migration run is required for either new or existing DBs.
- **Create a new migration (after changing models):**
  ```bash
  cd backend && alembic revision --autogenerate -m "describe_your_change"
  ```
  Review the generated file in `alembic/versions/`, then deploy; startup will apply it.

## Production

- **Startup:** The backend runs `alembic upgrade head` before serving traffic (with retries until the DB is reachable). This keeps the schema in sync with the code on every deploy.
- **Rollback:** Use `alembic downgrade -1` (or `alembic downgrade <revision>`) only when you have tested the downgrade path. Always back up the database before downgrading.
- **Multiple instances:** Safe. Alembic uses a single `alembic_version` table; the first process to run upgrade wins; others see the updated revision and no-op.

## Commands (run from `backend/`)

| Command | Purpose |
|--------|---------|
| `alembic upgrade head` | Apply all pending migrations (also runs on app startup). |
| `alembic downgrade -1` | Undo the last migration. |
| `alembic stamp head` | Mark DB as current without running migrations (for existing DBs). |
| `alembic current` | Show current revision. |
| `alembic history` | List revisions. |
| `alembic revision --autogenerate -m "message"` | Generate a new migration from model changes. |

## Revision chain

- `001_initial` – Creates all tables and enums from SQLAlchemy models.
- `002_indexes` – Adds indexes on `user_id`, `agent_id`, `job_id`, and related columns (issue #20).
- `003_legacy_001` … `019_legacy_019` – Idempotent conversions of legacy SQL 001–019 (pricing model, hiring, job_questions, jobs columns, agent_reviews, a2a_enabled, depends_on_previous, MCP tables, tool types, endpoint_path, allowed_tools, schema_metadata, pageindex, tool_visibility). Safe to run after 001_initial; no-op when objects already exist.

## Legacy databases

If your database was created with the old SQL migrations (001–019), startup detects existing tables and stamps the initial revision automatically; only pending Alembic revisions are applied. New schema changes must be done via Alembic only.
