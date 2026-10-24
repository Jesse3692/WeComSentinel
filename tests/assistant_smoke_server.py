"""Isolated browser fixture. Never seeds the configured business database."""

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base, BusinessEvent, Grant, Inbox, Job, Profile, Tenant, User, now_ms  # noqa: E402


async def prepare(app):
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with app.state.sessions() as db:
        db.add_all([
            Tenant(id=1, corp_id="SMOKE1", name="冒烟总部", credential_ref="SMOKE", callback_key="smoke1"),
            Tenant(id=2, corp_id="SMOKE2", name="冒烟分部", credential_ref="SMOKE", callback_key="smoke2"),
            User(id=1, ding_corp_id="DEMO", ding_user_id="admin", name="冒烟管理员", role="admin"),
            User(id=2, ding_corp_id="DEMO", ding_user_id="user", name="冒烟普通用户", role="user"),
        ])
        await db.flush()
        db.add(Grant(user_id=2, tenant_id=1))
        db.add_all([Profile(tenant_id=1, kind="group", external_id=f"smoke-group-{index:02d}",
                            name=f"冒烟客户群{index:02d}", data={"owner":"smoke-employee",
                            "member_list":[{"userid":"smoke-employee","type":1},
                                           {"userid":"smoke-customer","type":2}]}) for index in range(25)])
        db.add(Profile(tenant_id=2, kind="group", external_id="foreign-group", name="无权查看的群"))
        db.add_all([Profile(tenant_id=1, kind="employee", external_id="smoke-employee", name="模拟员工"),
                    Profile(tenant_id=1, kind="customer", external_id="smoke-customer", name="模拟客户",
                            data={"type":1,"follow_user":[{"userid":"smoke-employee","add_way":1}]})])
        for index, kind in enumerate(["contact_added", "contact_deleted", "group_left", "group_removed"]):
            inbox = Inbox(tenant_id=1, dedupe_key=f"smoke-{index}", payload_encrypted=b"synthetic", status="done")
            db.add(inbox)
            await db.flush()
            db.add(BusinessEvent(tenant_id=1, callback_id=inbox.id, item_key="smoke",
                event_type=kind, occurred_at=now_ms(), quantity=index + 1,
                employee_id="smoke-employee", customer_id="smoke-customer",
                group_id="smoke-group" if index >= 2 else None,
                resolution="inferred" if index >= 2 else "exact", enrichment_status="complete",
                deletion_direction="customer_deleted_employee" if index == 1 else None,
                snapshot={"demo": True, "employee_name": "模拟员工", "customer_name": "模拟客户", "group_name": "模拟客户群"}))
        failed=Inbox(tenant_id=1,dedupe_key="smoke-failed",payload_encrypted=b"synthetic",status="failed")
        db.add(failed)
        await db.flush()
        db.add(Job(tenant_id=1,callback_id=failed.id,status="failed",attempts=5,last_error="synthetic error"))
        await db.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model-base-url", default="")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="monitor-assistant-smoke-") as directory:
        settings = Settings(_env_file=None, app_env="test", secret_key="smoke-only-" * 5,
            payload_key=Fernet.generate_key().decode(),
            database_url=f"sqlite+aiosqlite:///{Path(directory) / 'smoke.db'}",
            media_dir=str(Path(directory) / "media"), demo_enabled=True,
            demo_password="assistant-smoke-only-password", worker_enabled=False,
            public_origin=f"http://127.0.0.1:{args.port}", assistant_base_url=args.model_base_url,
            assistant_api_key="", assistant_enabled=True, report_cache_seconds=0)
        app = create_app(settings)
        asyncio.run(prepare(app))
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
