"""Run migrations with the configured asynchronous PostgreSQL connection."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from prawdowid.config import get_settings
from prawdowid.infrastructure.db_models import metadata


def run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        version_table_schema=context.config.attributes.get("version_table_schema"),
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=get_settings().database_url,
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif (connection := context.config.attributes.get("connection")) is not None:
    run_migrations(connection)
else:
    asyncio.run(run_online())
