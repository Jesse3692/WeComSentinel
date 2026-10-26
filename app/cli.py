import argparse
import asyncio
import json
import secrets
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from sqlalchemy import select

from .config import Settings
from .db import make_database
from .models import BusinessEvent, Grant, Inbox, Profile, Tenant, User
from .platforms import Platforms
from .worker import Worker


async def seed(s):
    if not s.demo_enabled or s.app_env == "production":
        raise SystemExit("Demo must be explicitly enabled in a development environment")
    engine, sessions = make_database(s)
    async with sessions() as db:
        if await db.scalar(select(User.id).where(User.ding_corp_id == "DEMO")):
            print("Demo data already exists; no changes made")
            await engine.dispose()
            return
        tenants = [
            Tenant(
                corp_id=f"DEMO_CORP_{i}",
                name=name,
                credential_ref=f"DEMO{i}",
                callback_key=secrets.token_urlsafe(18),
            )
            for i, name in enumerate(["星河科技 · 总部", "星河科技 · 华东"], 1)
        ]
        db.add_all(tenants)
        admin = User(ding_corp_id="DEMO", ding_user_id="admin", name="演示管理员", role="admin")
        ordinary = User(ding_corp_id="DEMO", ding_user_id="user", name="演示主管", role="user")
        db.add_all([admin, ordinary])
        await db.flush()
        db.add(Grant(user_id=ordinary.id, tenant_id=tenants[0].id))
        today = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        names = ["陈雨", "林晓", "张宁", "李晨", "周悦", "王泽"]
        for tenant in tenants:
            for i, name in enumerate(names):
                db.add(
                    Profile(
                        tenant_id=tenant.id,
                        kind="employee",
                        external_id=f"employee{i}",
                        name=name,
                        data={"name": name},
                    )
                )
            for i in range(4):
                name = ["客户交流一群", "产品体验群", "售后服务群", "VIP 客户群"][i]
                db.add(
                    Profile(
                        tenant_id=tenant.id,
                        kind="group",
                        external_id=f"chat{i}",
                        name=name,
                        data={"name": name},
                    )
                )
            for day in range(60):
                for index, event_type in enumerate(
                    ["contact_added", "contact_deleted", "group_left", "group_removed"]
                ):
                    count = [
                        5 + (day * 7 + tenant.id) % 11,
                        (day + tenant.id) % 4,
                        (day * 3 + tenant.id) % 6,
                        (day + tenant.id) % 2,
                    ][index]
                    for item in range(count):
                        stamp = int(
                            (
                                today - timedelta(days=day) + timedelta(minutes=item * 7 + index)
                            ).timestamp()
                            * 1000
                        )
                        payload = {"demo": True, "type": event_type, "time": stamp, "item": item}
                        inbox = Inbox(
                            tenant_id=tenant.id,
                            dedupe_key=f"demo:{day}:{index}:{item}",
                            status="done",
                            payload_encrypted=Fernet(s.payload_key.encode()).encrypt(
                                json.dumps(payload).encode()
                            ),
                        )
                        db.add(inbox)
                        await db.flush()
                        person = (day + item) % len(names)
                        group = (day + item) % 4
                        snapshot = {
                            "employee_name": names[person],
                            "customer_name": f"客户 {day * 20 + item + 1:04d}",
                            "group_name": [
                                "客户交流一群",
                                "产品体验群",
                                "售后服务群",
                                "VIP 客户群",
                            ][group],
                            "customer_type": 1,
                            "gender": 0,
                            "company_name": "演示企业",
                            "demo": True,
                        }
                        db.add(
                            BusinessEvent(
                                tenant_id=tenant.id,
                                callback_id=inbox.id,
                                item_key="demo",
                                event_type=event_type,
                                employee_id=f"employee{person}" if index < 2 else None,
                                customer_id=f"customer{day * 20 + item}",
                                group_id=f"chat{group}" if index >= 2 else None,
                                operator_id=None,
                                deletion_direction=(
                                    "employee_deleted_customer"
                                    if item % 2
                                    else "customer_deleted_employee"
                                )
                                if index == 1
                                else None,
                                occurred_at=stamp,
                                add_way="1" if index == 0 else None,
                                channel_state="demo" if index == 0 else None,
                                quantity=1,
                                snapshot=snapshot,
                                resolution="exact",
                                enrichment_status="complete",
                            )
                        )
        await db.commit()
    await engine.dispose()
    print("Created 60 days of clearly labeled demo data, two tenants and two users")


async def bootstrap(s, args):
    engine, sessions = make_database(s)
    async with sessions() as db:
        user = await db.scalar(
            select(User).where(User.ding_corp_id == args.corp_id, User.ding_user_id == args.user_id)
        )
        if user:
            print("User already exists; no privileges changed")
        else:
            db.add(
                User(
                    ding_corp_id=args.corp_id,
                    ding_user_id=args.user_id,
                    name=args.name,
                    role="admin",
                )
            )
            await db.commit()
            print("Administrator created")
    await engine.dispose()


async def sync_profiles(s, args):
    engine, sessions = make_database(s)
    platforms = Platforms(s)
    worker = Worker(s, sessions, platforms)
    try:
        async with sessions() as db:
            tenant = await db.get(Tenant, args.tenant_id)
            if not tenant:
                raise SystemExit("Tenant does not exist")
        staff = await platforms.wecom(tenant, "externalcontact/get_follow_user_list")
        for uid in staff.get("follow_user", []):
            await worker.get_profile(tenant, "employee", uid)
            customers = await platforms.wecom(
                tenant, "externalcontact/list", params={"userid": uid}
            )
            for cid in customers.get("external_userid", []):
                await worker.get_profile(tenant, "customer", cid)
        cursor = ""
        while True:
            groups = await platforms.wecom(
                tenant,
                "externalcontact/groupchat/list",
                "POST",
                body={"limit": 100, "cursor": cursor},
            )
            for group in groups.get("group_chat_list", []):
                _, complete = await worker.sync_group(tenant, group["chat_id"])
                if not complete:
                    print(
                        "Group membership saved; some member avatars remain unavailable. Rerun sync to retry."
                    )
            cursor = groups.get("next_cursor", "")
            if not cursor:
                break
        print("Profile snapshot sync finished. This does not import historical events.")
    finally:
        await platforms.close()
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="企微流失监控管理工具")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate-keys")
    init = sub.add_parser("init-env")
    init.add_argument("--demo", action="store_true")
    sub.add_parser("init-db")
    sub.add_parser("seed-demo")
    admin = sub.add_parser("create-admin")
    admin.add_argument("--corp-id", required=True)
    admin.add_argument("--user-id", required=True)
    admin.add_argument("--name", required=True)
    sync = sub.add_parser("sync-profiles")
    sync.add_argument("--tenant-id", type=int, required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "generate-keys":
        print("SECRET_KEY=" + secrets.token_urlsafe(48))
        print("PAYLOAD_KEY=" + Fernet.generate_key().decode())
        return
    if args.command == "init-env":
        if Path(".env").exists():
            raise SystemExit(".env exists; refusing to overwrite")
        source = Path(".env.example").read_text(encoding="utf-8")
        source = source.replace(
            "SECRET_KEY=replace-with-a-random-secret-at-least-32-characters",
            "SECRET_KEY=" + secrets.token_urlsafe(48),
        )
        source = source.replace(
            "PAYLOAD_KEY=\n", "PAYLOAD_KEY=" + Fernet.generate_key().decode() + "\n"
        )
        if args.demo:
            source = source.replace("DEMO_ENABLED=false", "DEMO_ENABLED=true")
            source = source.replace(
                "DEMO_PASSWORD=\n", "DEMO_PASSWORD=" + secrets.token_urlsafe(18) + "\n"
            )
        Path(".env").write_text(source, encoding="utf-8")
        print("Created .env with random keys. Demo password (if enabled) is stored only in .env.")
        return
    s = Settings()
    s.prepare_dirs()
    if args.command == "init-db":
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    elif args.command == "seed-demo":
        asyncio.run(seed(s))
    elif args.command == "create-admin":
        asyncio.run(bootstrap(s, args))
    elif args.command == "sync-profiles":
        asyncio.run(sync_profiles(s, args))
    elif args.command == "backup":
        import shutil

        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=False)
        source = s.database_url.removeprefix("sqlite+aiosqlite:///")
        with sqlite3.connect(source) as db, sqlite3.connect(output / "monitor.db") as target:
            db.backup(target)
        shutil.copytree(s.media_dir, output / "media", dirs_exist_ok=True)
        print("Backup completed. Preserve PAYLOAD_KEY separately for recovery.")


if __name__ == "__main__":
    main()
