import base64
import os
import struct
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models import Base, BusinessEvent, Grant, Inbox, Job, Media, Profile, Session, Tenant, User, now_ms
from app.platforms import PlatformError
from app.security import signature
from app.worker import enqueue


@pytest.fixture
async def system(tmp_path, monkeypatch):
    key = base64.b64encode(bytes(range(32))).decode().rstrip("=")
    monkeypatch.setenv("WECOM_TEST_TOKEN", "callback-test-token")
    monkeypatch.setenv("WECOM_TEST_AES_KEY", key)
    monkeypatch.setenv("WECOM_TEST_SECRET", "secret-not-used")
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
                        media_dir=str(tmp_path / "media"), secret_key="x" * 40,
                        payload_key=Fernet.generate_key().decode(), public_origin="http://test",
                        demo_enabled=True, demo_password="demo-test-password", worker_enabled=False,
                        app_env="test")
    app = create_app(settings)
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with app.state.sessions() as db:
        db.add_all([Tenant(id=1, corp_id="corp1", name="主体一", credential_ref="TEST", callback_key="one"),
                    Tenant(id=2, corp_id="corp2", name="主体二", credential_ref="TEST", callback_key="two"),
                    User(id=1, ding_corp_id="DEMO", ding_user_id="admin", name="管理员", role="admin"),
                    User(id=2, ding_corp_id="DEMO", ding_user_id="user", name="普通用户", role="user")])
        await db.flush()
        db.add(Grant(user_id=2, tenant_id=1))
        await db.commit()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield app, client, key


async def login(client, role="admin"):
    response = await client.post("/api/v1/auth/demo", json={"password": "demo-test-password", "role": role})
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["data"]["csrf_token"]


def encrypted_message(key, xml, corp="corp1"):
    # Independent test-side encryption with the platform's random16/length/body/receiver layout.
    aes = base64.b64decode(key + "=")
    raw = os.urandom(16) + struct.pack("!I", len(xml)) + xml + corp.encode()
    padding = 32 - len(raw) % 32
    raw += bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(aes), modes.CBC(aes[:16])).encryptor()
    return base64.b64encode(encryptor.update(raw) + encryptor.finalize()).decode()


def callback_request(key, change="add_external_contact", customer="customer1", stamp=None):
    stamp = stamp or now_ms() // 1000
    xml = (f"<xml><ToUserName>corp1</ToUserName><CreateTime>{stamp}</CreateTime>"
           f"<Event>change_external_contact</Event><ChangeType>{change}</ChangeType>"
           f"<UserID>employee1</UserID><ExternalUserID>{customer}</ExternalUserID></xml>").encode()
    encrypted = encrypted_message(key, xml)
    params = {"timestamp": str(stamp), "nonce": "nonce", "msg_signature": signature("callback-test-token", str(stamp), "nonce", encrypted)}
    return params, f"<xml><Encrypt>{encrypted}</Encrypt></xml>"


async def insert_event(app, tenant=1, stamp=None, kind="contact_added", item="one", quantity=1):
    async with app.state.sessions() as db:
        inbox = Inbox(tenant_id=tenant, dedupe_key=os.urandom(8).hex(), payload_encrypted=b"test", status="done")
        db.add(inbox)
        await db.flush()
        e = BusinessEvent(tenant_id=tenant, callback_id=inbox.id, item_key=item, event_type=kind,
                          occurred_at=stamp or now_ms(), quantity=quantity, employee_id="employee1",
                          group_id="chat1" if kind.startswith("group") else None,
                          snapshot={"employee_name": "员工一", "customer_name": "客户一", "group_name": "客户群"},
                          enrichment_status="complete")
        db.add(e)
        await db.commit()
        return e.id


async def test_permissions_csrf_revocation_and_logout(system):
    app, client, _ = system
    foreign = await insert_event(app, tenant=2)
    await login(client, "user")
    assert len((await client.get("/api/v1/tenants")).json()["data"]) == 1
    assert (await client.get("/api/v1/events?tenant_id=2")).status_code == 403
    assert (await client.get(f"/api/v1/events/{foreign}")).status_code == 404
    assert (await client.get("/api/v1/admin/users")).status_code == 403
    client.headers.pop("x-csrf-token")
    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 403
    me = (await client.get("/api/v1/me")).json()["data"]
    client.headers["x-csrf-token"] = me["csrf_token"]
    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 200
    assert (await client.get("/api/v1/me")).status_code == 401
    await login(client)
    assert (await client.put("/api/v1/admin/users/2/tenants", json={"tenant_ids": []})).status_code == 200
    await login(client, "user")
    assert (await client.get("/api/v1/events?tenant_id=1")).status_code == 403


async def test_origin_and_expiration(system):
    app, client, _ = system
    assert (await client.post("/api/v1/auth/demo", headers={"Origin": "https://evil.example"},
                             json={"password":"demo-test-password"})).status_code == 403
    await login(client)
    async with app.state.sessions() as db:
        sessions = (await db.scalars(select(Session))).all()
        for session in sessions:
            session.expires_at = 1
        await db.commit()
    assert (await client.get("/api/v1/me")).status_code == 401


async def test_callback_verification_dedupe_and_recovery(system):
    app, client, key = system
    async def employee(*args):
        return {"name": "测试员工"}
    async def customer(*args):
        return {"external_contact": {"name": "测试客户", "type": 1}, "follow_user": [{"userid": "employee1", "add_way": 1}]}
    app.state.platforms.employee = employee
    app.state.platforms.customer = customer
    params, body = callback_request(key)
    assert (await client.post("/callbacks/wecom/one", params={**params, "msg_signature": "invalid"}, content=body)).status_code == 400
    for _ in range(3):
        assert (await client.post("/callbacks/wecom/one", params=params, content=body)).text == "success"
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Inbox)) == 1
        job = await db.scalar(select(Job))
        job.status = "running"
        job.lease_until = 1
        await db.commit()
    assert await app.state.worker.tick()
    async with app.state.sessions() as db:
        rows = (await db.scalars(select(BusinessEvent))).all()
        assert len(rows) == 1
        assert rows[0].snapshot["customer_name"] == "测试客户"
        assert rows[0].add_way == "1"
        job = await db.scalar(select(Job))
        assert job.status == "done"
        job.status, job.next_run_at = "retry", 1
        await db.commit()
    assert await app.state.worker.tick()
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 1
    # A different customer in the same second must be retained.
    p2, b2 = callback_request(key, customer="customer2", stamp=int(params["timestamp"]))
    assert (await client.post("/callbacks/wecom/one", params=p2, content=b2)).status_code == 200
    assert await app.state.worker.tick()
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 2


async def test_callback_url_validation_and_cross_corp(system):
    _, client, key = system
    encrypted = encrypted_message(key, b"challenge")
    params = {"timestamp":"123", "nonce":"n", "echostr":encrypted,
              "msg_signature":signature("callback-test-token", "123", "n", encrypted)}
    assert (await client.get("/callbacks/wecom/one", params=params)).text == "challenge"
    assert (await client.get("/callbacks/wecom/two", params=params)).status_code == 400


async def test_deletion_uses_snapshot_and_keeps_direction(system):
    app, client, key = system
    async with app.state.sessions() as db:
        db.add_all([Profile(tenant_id=1, kind="employee", external_id="employee1", name="员工", data={"name":"员工"}),
                    Profile(tenant_id=1, kind="customer", external_id="customer1", name="历史客户", data={"name":"历史客户"})])
        await db.commit()
    p, b = callback_request(key, change="del_follow_user")
    await client.post("/callbacks/wecom/one", params=p, content=b)
    await app.state.worker.tick()
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        assert row.deletion_direction == "customer_deleted_employee"
        assert row.snapshot["customer_name"] == "历史客户"


async def test_group_does_not_invent_members_or_operator(system):
    app, client, _ = system
    async def group(*args):
        return {"name": "真实群名", "owner": "not-necessarily-operator", "member_list": []}
    app.state.platforms.group = group
    async with app.state.sessions() as db:
        await enqueue(db, app.state.settings, 1, {"Event":"change_external_chat", "ChangeType":"update",
            "CreateTime":str(now_ms() // 1000), "ChatId":"chat1", "UpdateDetail":"del_member", "QuitScene":"1", "MemChangeCnt":"3"})
    await app.state.worker.tick()
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        assert row.event_type == "group_change_unknown"
        assert row.snapshot["reported_event_type"] == "group_removed"
        assert row.quantity == 3
        assert row.customer_id is None and row.operator_id is None
        assert row.resolution == "unresolved"


async def test_dashboard_matches_details_and_previous_period(system):
    app, client, _ = system
    stamp = int(datetime(2026, 1, 1, 12, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
    await insert_event(app, stamp=stamp, quantity=2)
    await insert_event(app, stamp=stamp-86_400_000, quantity=1)
    await insert_event(app, tenant=2, stamp=stamp, quantity=90)
    await login(client, "user")
    query = "?start_date=2026-01-01&end_date=2026-01-01"
    dashboard = (await client.get("/api/v1/dashboard"+query)).json()["data"]
    detail = (await client.get("/api/v1/events"+query)).json()["data"]
    assert dashboard["cards"][0] == {"type":"contact_added", "value":2, "delta":1}
    assert dashboard["trend"][0]["contact_added"] == 2
    assert dashboard["tops"]["contact_added"][0]["count"] == detail["total_quantity"] == 2
    assert (await client.get("/api/v1/dashboard?start_date=2026-02-01&end_date=2026-01-01")).status_code == 422


async def test_cursor_stable_on_late_insert_and_filter_bound(system):
    app, client, _ = system
    now = now_ms()
    ids = [await insert_event(app, stamp=now-i*1000) for i in range(5)]
    await login(client)
    first = (await client.get("/api/v1/events?limit=2")).json()["data"]
    late = await insert_event(app, stamp=now-2500)
    cursor = first["next_cursor"]
    second = (await client.get("/api/v1/events", params={"limit":10, "cursor":cursor})).json()["data"]
    retrieved = [r["id"] for r in first["items"] + second["items"]]
    assert retrieved == ids and late not in retrieved
    assert (await client.get("/api/v1/events", params={"cursor":cursor, "event_type":"contact_deleted"})).status_code == 422
    assert (await client.get("/api/v1/events", params={"cursor":cursor+"x"})).status_code == 422


def test_production_rejects_demo_and_insecure_cookies():
    s = Settings(_env_file=None, app_env="production", demo_enabled=True, cookie_secure=False,
                 secret_key="x"*40, payload_key=Fernet.generate_key().decode())
    with pytest.raises(RuntimeError):
        s.validate_runtime()


async def test_profile_failure_keeps_event_and_retry_job(system):
    app, client, key = system
    async def fail(*args):
        raise PlatformError("platform_error_48002")
    app.state.platforms.employee = fail
    app.state.platforms.customer = fail
    p, b = callback_request(key)
    await client.post("/callbacks/wecom/one", params=p, content=b)
    await app.state.worker.tick()
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        job = await db.scalar(select(Job))
        assert row.enrichment_status == "partial"
        assert job.status == "retry"
        assert job.next_run_at > now_ms()
        job.attempts, job.next_run_at = 4, 1
        await db.commit()
    await app.state.worker.tick()
    await login(client)
    jobs = (await client.get("/api/v1/admin/jobs")).json()["data"]
    assert jobs[0]["status"] == "failed"
    assert (await client.post(f"/api/v1/admin/jobs/{jobs[0]['id']}/retry", json={})).status_code == 200
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 1
        assert (await db.scalar(select(Job))).status == "pending"


async def test_media_access_is_tenant_scoped(system):
    app, client, _ = system
    async with app.state.sessions() as db:
        db.add(Media(id=1, tenant_id=2, sha256="checksum", relative_path="missing.jpg", mime_type="image/jpeg", size_bytes=1))
        await db.commit()
    await login(client, "user")
    assert (await client.get("/api/v1/media/1")).status_code == 404


async def test_tenant_scoped_employee_filter_and_invalid_grants(system):
    app, client, _ = system
    await insert_event(app, tenant=1)
    await insert_event(app, tenant=2)
    await login(client)
    response = await client.get("/api/v1/events?employee_id=employee1&employee_tenant_id=2")
    assert {r["tenant_id"] for r in response.json()["data"]["items"]} == {2}
    assert (await client.put("/api/v1/admin/users/2/tenants", json={"tenant_ids":[999]})).status_code == 422
    async with app.state.sessions() as db:
        assert (await db.scalar(select(Grant).where(Grant.user_id==2))).tenant_id == 1


async def test_dingtalk_identity_must_match_preapproved_user(system):
    app, client, _ = system
    async def identity(code):
        return {"corp_id":"company", "user_id":"unapproved", "name":"陌生人", "avatar":""}
    app.state.platforms.ding_identity = identity
    assert (await client.post("/api/v1/auth/dingtalk", json={"code":"valid-platform-code"})).status_code == 403
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Session)) == 0


async def test_avatar_failure_stays_partial_on_cached_retry(system):
    app, _, _ = system
    async with app.state.sessions() as db:
        tenant = await db.get(Tenant,1)
        db.add(Profile(tenant_id=1,kind="customer",external_id="customer1",name="客户",
                       data={"name":"客户","avatar":"https://example.invalid/avatar.jpg","media_id":None}))
        await db.commit()
    async def fail(*args):
        raise PlatformError("platform_transport_error")
    app.state.platforms.customer = fail
    data, complete = await app.state.worker.get_profile(tenant,"customer","customer1")
    assert data["name"] == "客户" and not complete
