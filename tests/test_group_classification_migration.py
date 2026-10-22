"""Exercise the actual Alembic upgrade against a historical source deployment."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, func, insert, select

from app.models import BusinessEvent, Inbox, Tenant


ROOT = Path(__file__).resolve().parents[1]


def test_historical_classification_upgrade_and_restore(tmp_path):
    db_path = tmp_path / "history.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
                   "MEDIA_DIR": str(tmp_path / "media")}

    def alembic(*args):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT,
                                env=environment, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr

    alembic("upgrade", "0001")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    original = {"group_name": "历史群", "reported_event_type": "group_removed",
                "member_evidence": "snapshot_difference", "member_candidates": [
                    {"name": "旧版客户", "member_type": 2},
                    {"member_id": "staff", "name": "员工", "member_type": 1}],
                "note": "旧提示不计入统计"}
    with engine.begin() as connection:
        connection.execute(insert(Tenant).values(id=1, name="测试", corp_id="corp",
            credential_ref="TEST", callback_key="test"))
        for callback_id in (1, 2, 3):
            connection.execute(insert(Inbox).values(id=callback_id, tenant_id=1,
                dedupe_key=str(callback_id), payload_encrypted=b"not-a-real-callback"))
        connection.execute(insert(BusinessEvent).values(id=1, tenant_id=1, callback_id=1,
            item_key="group", event_type="group_change_unknown", quantity=2,
            resolution="unresolved", enrichment_status="partial", group_id="chat", occurred_at=123,
            snapshot=original))
        connection.execute(insert(BusinessEvent).values(id=2, tenant_id=1, callback_id=2,
            item_key="group", event_type="group_change_unknown", quantity=1,
            resolution="unresolved", enrichment_status="partial", group_id="chat", occurred_at=124,
            snapshot={"member_candidates": [], "member_evidence": "no_before_snapshot"}))
        connection.execute(insert(BusinessEvent).values(id=3, tenant_id=1, callback_id=3,
            item_key="contact", event_type="contact_added", quantity=1,
            resolution="exact", enrichment_status="complete", occurred_at=125, snapshot={}))
    alembic("upgrade", "head")
    table = BusinessEvent.__table__
    with engine.begin() as connection:
        rows = connection.execute(select(table).order_by(table.c.id)).mappings().all()
        assert len(rows) == 4
        assert rows[0]["id"] == 1 and rows[0]["event_type"] == "group_removed"
        assert rows[0]["quantity"] == 1 and rows[0]["resolution"] == "inferred"
        assert rows[1]["event_type"] == "group_change_unknown"
        assert rows[2]["event_type"] == "contact_added"
        assert rows[3]["item_key"] == "group_other" and rows[3]["quantity"] == 1
        assert rows[0]["snapshot"]["reported_quantity"] == 2
        assert rows[3]["snapshot"]["classification_reason"] == "internal_members"
        # Repeating the backfill function itself does not duplicate a split row.
        spec = importlib.util.spec_from_file_location("group_migration", ROOT /
            "migrations/versions/0002_customer_group_classification.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.reclassify(connection)
        assert connection.scalar(select(func.count()).select_from(table)) == 4
    alembic("upgrade", "head")
    alembic("downgrade", "0001")
    with engine.connect() as connection:
        rows = connection.execute(select(table).order_by(table.c.id)).mappings().all()
        assert len(rows) == 3
        assert rows[0]["snapshot"] == original
        assert rows[0]["quantity"] == 2 and rows[0]["event_type"] == "group_change_unknown"
        assert rows[1]["snapshot"] == {"member_candidates": [], "member_evidence": "no_before_snapshot"}
    engine.dispose()
