# Agent reviews migrations

Migrations for the agent ratings/reviews feature live in **`backend/migrations/`**:

- **`009_add_agent_reviews_table.sql`** – creates `agent_reviews` (run if you get "relation agent_reviews does not exist").
- **`010_drop_agent_review_unique_constraint.sql`** – allows multiple reviews per user per agent (run if you get "duplicate key ... uq_agent_review_user").

## Run (Docker)

```bash
docker-compose exec -T db psql -U postgres -d agent_marketplace < backend/migrations/009_add_agent_reviews_table.sql
docker-compose exec -T db psql -U postgres -d agent_marketplace < backend/migrations/010_drop_agent_review_unique_constraint.sql
```

## Run (local PostgreSQL)

```bash
psql -U postgres -d agent_marketplace -f backend/migrations/009_add_agent_reviews_table.sql
psql -U postgres -d agent_marketplace -f backend/migrations/010_drop_agent_review_unique_constraint.sql
```

See **`backend/migrations/README.md`** for running all migrations in order.
