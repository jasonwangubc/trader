"""Test fixtures.

Strategy: schema is built once per pytest session against a dedicated
`trader_test` database (created on the fly if missing) using a *synchronous*
engine — no event loop involved. Each test then gets its own short-lived
async engine + session, and all tables are truncated on teardown. This keeps
every test isolated without fighting pytest-asyncio loop scopes, and works
with services that call `session.commit()` internally.

Requires the Docker Postgres from docker-compose.yml (port 5433). Override
with TEST_DATABASE_URL / TEST_DATABASE_URL_SYNC if yours differs.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import Base

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://trader:trader@localhost:5433/trader_test",
)
TEST_DB_URL_SYNC = os.environ.get(
    "TEST_DATABASE_URL_SYNC",
    "postgresql+psycopg2://trader:trader@localhost:5433/trader_test",
)
# Admin connection used only to CREATE DATABASE trader_test if it's missing.
ADMIN_DB_URL_SYNC = os.environ.get(
    "TEST_ADMIN_DATABASE_URL_SYNC",
    "postgresql+psycopg2://trader:trader@localhost:5433/trader",
)


def _ensure_test_database() -> None:
    admin = create_engine(ADMIN_DB_URL_SYNC, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = 'trader_test'")
            ).scalar()
            if not exists:
                conn.execute(text("CREATE DATABASE trader_test"))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def _database() -> None:
    """Create the test DB + fresh schema once per test session (sync, no loop)."""
    _ensure_test_database()
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        # Fresh schema every session so model changes don't require manual resets.
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


@pytest.fixture
async def db_session(_database):
    """Function-scoped async session on its own engine; truncates on teardown."""
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        async with maker() as session:
            yield session
        # Services commit internally, so rollback isn't enough — wipe all rows.
        table_list = ", ".join(t.name for t in Base.metadata.sorted_tables)
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {table_list} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()
