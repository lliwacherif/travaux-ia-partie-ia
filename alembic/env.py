"""Alembic migration environment.

This is wired to ``app.core.config.settings`` so the DSN and the target
metadata always match the running application - no copy-pasted URLs in
``alembic.ini``.

Notes:
* We use the **synchronous** DSN here (``SYNC_DATABASE_URL``). Alembic runs
  on top of psycopg2, not asyncpg, so the value is auto-derived from the
  async DSN by the settings validator.
* We import ``app.models`` so every ORM class registers itself on
  ``Base.metadata`` before autogenerate scans it.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# ---------------------------------------------------------------------------
# Make sure the project root is on sys.path so ``app.*`` imports resolve when
# Alembic spawns env.py as a standalone script.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.db.database import Base  # noqa: E402
from app import models  # noqa: E402,F401  (registers models on Base.metadata)


config = context.config

# Inject our dynamic DSN so alembic.ini's ``sqlalchemy.url`` stays empty.
config.set_main_option("sqlalchemy.url", str(settings.SYNC_DATABASE_URL))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits raw SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
