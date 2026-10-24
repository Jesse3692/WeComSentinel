from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def make_database(settings):
    settings.prepare_dirs()
    kwargs = {}
    # A shared-cache file database in WAL mode allows many concurrent readers;
    # the default pool (5+10) would queue report traffic behind a handful of
    # connections. In-memory databases must keep a single connection.
    if settings.database_url.startswith("sqlite+aiosqlite:") and ":memory:" not in settings.database_url:
        kwargs["pool_size"] = max(1, settings.db_pool_size)
        kwargs["max_overflow"] = max(0, settings.db_max_overflow)
    engine = create_async_engine(settings.database_url, **kwargs)

    @event.listens_for(engine.sync_engine, "connect")
    def pragmas(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.close()

    return engine, async_sessionmaker(engine, expire_on_commit=False)
