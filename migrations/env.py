import asyncio

from alembic import context

from app.config import get_settings
from app.db import make_database
from app.models import Base


def sync_migrations(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run():
    engine, _ = make_database(get_settings())
    async with engine.connect() as connection:
        await connection.run_sync(sync_migrations)
    await engine.dispose()


asyncio.run(run())
