"""Alembic environment for the isolated MySQL 8 MDM schema."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from webapp.mdm.database import get_database_url
from webapp.mdm.models import Base
from webapp.sales import models as sales_models  # noqa: F401 - register Sales tables
from webapp.ordering import models as ordering_models  # noqa: F401 - register Ordering tables


config = context.config
target_metadata = Base.metadata


def configured_database_url():
    # Alembic ConfigParser treats percent signs as interpolation markers.
    return get_database_url().replace("%", "%%")


def run_migrations_offline():
    context.configure(
        url=configured_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    config.set_main_option("sqlalchemy.url", configured_database_url())
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
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
