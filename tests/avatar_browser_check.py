"""Run the real media endpoint + browser against a disposable local database."""

import asyncio
import base64
import hashlib
import os
from pathlib import Path
import sys
import tempfile

import uvicorn
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.main import create_app
from app.models import Base, BusinessEvent, Grant, Inbox, Media, Tenant, User, now_ms


async def main():
    with tempfile.TemporaryDirectory(prefix="avatar-regression-") as directory:
        root = Path(directory)
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH1cAAAAASUVORK5CYII="
        )
        (root / "avatar.png").write_bytes(image)
        settings = Settings(
            _env_file=None,
            app_env="test",
            worker_enabled=False,
            database_url=f"sqlite+aiosqlite:///{root / 'test.db'}",
            media_dir=str(root),
            secret_key="local-test-only-" * 4,
            payload_key=Fernet.generate_key().decode(),
            public_origin="http://127.0.0.1:8011",
            demo_enabled=True,
            demo_password="local-test-password",
        )
        app = create_app(settings)
        async with app.state.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.sessions() as db:
            db.add_all(
                [
                    Tenant(
                        id=t,
                        name=f"模拟主体{t}",
                        corp_id=f"corp{t}",
                        credential_ref="TEST",
                        callback_key=f"key{t}",
                    )
                    for t in (1, 2)
                ]
            )
            db.add(
                User(id=1, name="模拟用户", ding_corp_id="DEMO", ding_user_id="user", role="user")
            )
            await db.flush()
            db.add(Grant(user_id=1, tenant_id=1))
            db.add_all(
                [
                    Media(
                        id=t,
                        tenant_id=t,
                        sha256=hashlib.sha256(image).hexdigest(),
                        relative_path="avatar.png",
                        mime_type="image/png",
                        size_bytes=len(image),
                    )
                    for t in (1, 2)
                ]
            )
            inbox = Inbox(tenant_id=1, dedupe_key="fixture", payload_encrypted=b"fixture")
            db.add(inbox)
            await db.flush()
            db.add(
                BusinessEvent(
                    tenant_id=1,
                    callback_id=inbox.id,
                    item_key="group",
                    event_type="group_left",
                    quantity=1,
                    occurred_at=now_ms(),
                    group_id="test",
                    resolution="inferred",
                    enrichment_status="complete",
                    snapshot={
                        "group_name": "模拟群",
                        "member_evidence": "snapshot_difference",
                        "member_candidates": [
                            {
                                "member_id": "test-customer",
                                "member_type": 2,
                                "name": "模拟客户",
                                "media_id": 1,
                            }
                        ],
                    },
                )
            )
            await db.commit()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8011, log_level="error"))
        task = asyncio.create_task(server.serve())
        try:
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("local fixture server did not start")
                await asyncio.sleep(0.05)
            process = await asyncio.create_subprocess_exec(
                "node", "tests/avatar-media.cjs", env=os.environ
            )
            if await process.wait():
                raise RuntimeError("avatar browser regression failed")
        finally:
            server.should_exit = True
            await task


if __name__ == "__main__":
    asyncio.run(main())
