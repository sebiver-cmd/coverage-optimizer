"""Alembic environment configuration for SB-Optima.

Reads ``DATABASE_URL`` from the process environment so that the connection
string is never hard-coded.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import the shared declarative base so autogenerate can detect models.
from backend.db import Base  # noqa: F401 - side-effect: registers metadata

# Import models so their tables are registered on Base.metadata.
import backend.models  # noqa: F401

# Alembic Config object — gives access to alembic.ini values.
config = context.config

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# MetaData for autogenerate support
target_metadata = Base.metadata


def _get_url() -> str:
    """Return the database URL from the environment (or alembic.ini fallback)."""
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url
    # Fall back to whatever is in alembic.ini (usually empty during local dev)
    return config.get_main_option("sqlalchemy.url", "")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Generates SQL scripts without connecting to the database.
    """
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates an engine and associates a connection with the migration context.
    """
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
