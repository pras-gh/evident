"""Alembic environment.

The URL comes from DATABASE_URL rather than alembic.ini so the same chain runs
against local, CI and production without editing a checked-in file.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from evident_db.base import Base
from evident_db import models  # noqa: F401  — registers every table

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

url = os.environ.get("DATABASE_URL", "postgresql+psycopg://localhost/evident")
if url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
config.set_main_option("sqlalchemy.url", url)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Emit pgvector types with their import.

    Without this, autogenerate writes `pgvector.sqlalchemy.vector.VECTOR(...)`
    into a migration that never imports pgvector, and the revision fails with a
    NameError the first time anyone runs it on a fresh database. Autogenerate
    only imports what it knows about, so third-party types have to be declared.
    """
    if type_ == "type" and obj.__class__.__module__.startswith("pgvector"):
        autogen_context.imports.add("import pgvector.sqlalchemy")
        return f"pgvector.sqlalchemy.Vector(dim={obj.dim})"
    return False


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata,
                      literal_binds=True, compare_type=True,
                      render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}),
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, compare_server_default=True,
                          render_item=render_item)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
