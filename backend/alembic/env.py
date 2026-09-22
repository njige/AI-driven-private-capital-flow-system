import sys
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# ------------------------------------------------------------------
# 1. Path Setup & Model Registration
# ------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db.database import Base
import app.db.models  # Registers FilingRecord with Base.metadata

# Read DATABASE_URL from environment variable, or default to local PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/pcf_db")

# ------------------------------------------------------------------
# 2. Alembic Configuration
# ------------------------------------------------------------------
config = context.config

# Convert async driver notation if present, since standard Alembic runner uses sync connection
sync_url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
config.set_main_option("sqlalchemy.url", sync_url)

# Setup loggers
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Filter out Superset and internal tables so Alembic doesn't drop them
def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table":
        ignored_prefixes = ("ab_", "dashboard", "report", "slice", "sqla", "tag", "task", "rls_")
        ignored_exact = {
            "css_templates", "table_schema", "query", "keyvalue", "sql_metrics",
            "saved_query", "ssh_tunnels", "cache_keys", "tables", "dashboards",
            "tab_state", "key_value", "annotation_layer", "table_columns", "dbs",
            "embedded_dashboards", "user_attribute", "slices", "logs", "favstar",
            "dynamic_plugin", "database_user_oauth2_tokens", "user_favorite_tag",
            "annotation", "tagged_object", "row_level_security_filters", "themes"
        }
        if name in ignored_exact or any(name.startswith(p) for p in ignored_prefixes):
            return False
    return True


# ------------------------------------------------------------------
# 3. Migration Runners
# ------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, 
            target_metadata=target_metadata,
            include_object=include_object
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()