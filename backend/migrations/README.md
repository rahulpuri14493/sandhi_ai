# Database migrations

All SQL migrations live here. **Run in order** (001 → 017) when setting up or updating the database. Each file is idempotent where possible (`IF NOT EXISTS`, `IF EXISTS`).

## Order

| File | Description |
|------|-------------|
| `001_add_pricing_model_column.sql` | Agents: pricing_model enum, monthly_price, quarterly_price |
| `002_fix_pricingmodel_enum.sql` | Fix pricingmodel enum (only if 001 caused issues) |
| `003_add_api_key_column.sql` | Agents: api_key column |
| `004_add_hiring_tables.sql` | hiring_positions, agent_nominations tables |
| `005_create_job_qa_table.sql` | job_questions table |
| `006_add_conversation_column.sql` | Jobs: conversation column |
| `007_add_job_files_column.sql` | Jobs: files column |
| `008_add_failure_reason_column.sql` | Jobs: failure_reason column |
| `009_add_agent_reviews_table.sql` | agent_reviews table (ratings/reviews) |
| `010_drop_agent_review_unique_constraint.sql` | Allow multiple reviews per user per agent |
| `011_add_a2a_enabled_column.sql` | Agents: a2a_enabled (A2A protocol support) |
| `012_add_workflow_step_depends_on_previous.sql` | Workflow steps: dependency support |
| `013_add_mcp_tables.sql` | MCP: mcp_server_connections, mcp_tool_configs (encrypted credentials per user) |
| `014_add_mcp_tool_types.sql` | MCP: add tool types (pinecone, weaviate, qdrant, chroma, mysql, elasticsearch, s3, slack, github, notion, rest_api) |
| `015_add_mcp_endpoint_path.sql` | MCP connections: endpoint_path |
| `016_add_job_and_step_allowed_tools.sql` | Jobs/workflow steps: allowed_platform_tool_ids, allowed_connection_ids |
| `017_add_tool_schema_and_business_context.sql` | MCP tool configs: schema_metadata, business_description (for SQL tools) |

**Prerequisites:** Core tables (`users`, `agents`, `jobs`, etc.) must exist. The app creates them via `Base.metadata.create_all()` on startup; if you use a blank DB, start the app once so core tables exist, then run migrations.

**MCP (013):** The backend automatically runs `013_add_mcp_tables.sql` on startup if the `mcp_server_connections` table does not exist. You do not need to run 013 manually when using Docker or the default backend startup.

## Run all migrations (Docker)

```bash
# From project root
for f in backend/migrations/*.sql; do
  docker-compose exec -T db psql -U postgres -d agent_marketplace < "$f"
done
```

Or run one file:

```bash
docker-compose exec db psql -U postgres -d agent_marketplace -f /path/inside/container/001_add_pricing_model_column.sql
```

Copy a file into the container first if needed:

```bash
docker cp backend/migrations/009_add_agent_reviews_table.sql $(docker-compose ps -q db):/tmp/
docker-compose exec db psql -U postgres -d agent_marketplace -f /tmp/009_add_agent_reviews_table.sql
```

## Run all migrations (local PostgreSQL)

```bash
# From project root
for f in backend/migrations/*.sql; do
  psql -U postgres -d agent_marketplace -f "$f"
done
```

Or run one file:

```bash
psql -U postgres -d agent_marketplace -f backend/migrations/009_add_agent_reviews_table.sql
```

Use your actual DB user and database name if different.

## Verify

```bash
# Docker
docker-compose exec db psql -U postgres -d agent_marketplace -c "\dt"

# Local
psql -U postgres -d agent_marketplace -c "\dt"
```

After migrations, restart the backend so the app uses the updated schema.
