from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def make_database(settings):
    settings.prepare_dirs()
    engine = create_async_engine(settings.database_url)

    @event.listens_for(engine.sync_engine, "connect")
    def pragmas(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.close()

    return engine, async_sessionmaker(engine, expire_on_commit=False)
