"""
Alembic migration environment. Uses the app's DATABASE_URL and Base.metadata.
Run from backend/ or with prepend_sys_path so db.* and models are importable.
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import context
from core.config import settings

# Alembic Config object
config = context.config

# Set sqlalchemy.url from centralized app settings (same as db.database)
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import Base and all models so target_metadata is complete (run from backend/ or with path set)
from db.database import Base
from models import (  # noqa: F401
    User,
    Agent,
    Job,
    WorkflowStep,
    AgentReview,
    AgentCommunication,
    Transaction,
    Earnings,
    AuditLog,
    HiringPosition,
    AgentNomination,
    JobQuestion,
    MCPServerConnection,
    MCPToolConfig,
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL only, no DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to DB and run)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
