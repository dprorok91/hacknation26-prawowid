import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {
    "sources",
    "documents",
    "document_versions",
    "passages",
    "events",
    "event_documents",
    "analyses",
    "claims",
    "evidence",
    "narrative_frames",
    "jobs",
    "audit_events",
    "discovery_cache",
    "provider_daily_usage",
}


def migration_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def test_migration_generates_postgresql_ddl(capsys):
    command.upgrade(migration_config(), "head", sql=True)
    ddl = capsys.readouterr().out.lower()
    assert "create extension if not exists vector" in ddl
    assert "vector(384)" in ddl
    assert "create table jobs" in ddl
    assert "unique (kind, dedupe_key, generation)" in ddl
    assert "using gin" in ddl


@pytest.mark.asyncio
async def test_initial_migration_on_postgresql():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run the PostgreSQL migration checks.")

    schema = f"prawdowid_migration_{uuid4().hex}"
    engine = create_async_engine(url)
    config = migration_config()
    config.attributes["version_table_schema"] = schema

    async def apply(change, destination):
        async with engine.begin() as conn:
            await conn.execute(text(f'SET LOCAL search_path = "{schema}", public'))

            def run(sync_conn):
                config.attributes["connection"] = sync_conn
                change(config, destination)

            await conn.run_sync(run)

    try:
        async with engine.begin() as connection:
            assert 160000 <= int(await connection.scalar(text("SHOW server_version_num"))) < 170000
            await connection.execute(CreateSchema(schema))
        await apply(command.upgrade, "head")
        async with engine.connect() as connection:

            def read_schema(sync_conn):
                db = inspect(sync_conn)
                return (
                    set(db.get_table_names(schema=schema)),
                    {
                        table: {
                            tuple(entry["column_names"])
                            for entry in db.get_unique_constraints(table, schema=schema)
                        }
                        for table in (
                            "documents",
                            "document_versions",
                            "passages",
                            "analyses",
                            "jobs",
                        )
                    },
                    db.get_pk_constraint("document_versions", schema=schema)["constrained_columns"],
                )

            tables, constraints, version_key = await connection.run_sync(read_schema)
            assert EXPECTED_TABLES <= tables
            assert ("canonical_url",) in constraints["documents"]
            assert version_key == ["document_id", "content_version"]
            assert ("document_id", "content_version", "ordinal") in constraints["passages"]
            assert ("cache_key",) in constraints["analyses"]
            assert ("kind", "dedupe_key", "generation") in constraints["jobs"]
            assert (
                await connection.scalar(
                    text(f"""
                SELECT format_type(atttypid, atttypmod) FROM pg_attribute
                WHERE attrelid = '{schema}.passages'::regclass AND attname = 'embedding'
            """)
                )
                == "vector(384)"
            )
        await apply(command.downgrade, "base")
        async with engine.connect() as connection:
            assert await connection.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names(schema=schema)
            ) == ["alembic_version"]
            assert await connection.scalar(
                text("SELECT EXISTS (SELECT FROM pg_extension WHERE extname='vector')")
            )
        await apply(command.upgrade, "head")
        async with engine.connect() as connection:
            assert EXPECTED_TABLES <= set(
                await connection.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_table_names(schema=schema)
                )
            )
    finally:
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        await engine.dispose()
