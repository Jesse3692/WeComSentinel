"""Replay upgrade/downgrade without importing evolving application models."""
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]


def test_capture_version_upgrade_downgrade_and_repeated_upgrade(tmp_path):
    db_path = tmp_path / "capture.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
                   "MEDIA_DIR": str(tmp_path / "media")}

    def alembic(*args):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT,
                                env=environment, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr

    alembic("upgrade", "0002_customer_groups")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO tenants (id, corp_id, name, credential_ref, callback_key, "
                                "enabled, created_at) VALUES (1, 'test', '主体', 'TEST', 'key', 1, 1)"))
        connection.execute(text("INSERT INTO profiles (tenant_id, kind, external_id, name, data, synced_at) "
                                "VALUES (1, 'group', 'group', '历史群', :data, 1234)"),
                           {"data": '{"member_list": [{"userid": "before", "type": 2}]}'})
        before = connection.execute(text("SELECT id, name, data, synced_at FROM profiles")).one()
    alembic("upgrade", "head")
    alembic("upgrade", "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id, name, data, synced_at FROM profiles")).one() == before
        assert connection.scalar(text("SELECT capture_version FROM profiles")) == 0
    alembic("downgrade", "0002_customer_groups")
    with engine.connect() as connection:
        assert "capture_version" not in {column["name"] for column in inspect(connection).get_columns("profiles")}
        assert connection.execute(text("SELECT id, name, data, synced_at FROM profiles")).one() == before
    alembic("upgrade", "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT capture_version FROM profiles")) == 0
        assert connection.execute(text("SELECT id, name, data, synced_at FROM profiles")).one() == before
    engine.dispose()
