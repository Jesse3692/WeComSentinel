"""Event classification and recovery tests: mocked platform, real database/worker/API."""
import pytest
from sqlalchemy import func, select

from app.models import BusinessEvent, Job, Profile, now_ms
from app.platforms import PlatformError
from app.worker import enqueue
import test_system
from test_system import login

system = test_system.system


def group_payload(**overrides):
    return {"Event": "change_external_chat", "ChangeType": "update", "UpdateDetail": "del_member",
            "ChatId": "chat", "CreateTime": str(now_ms() // 1000), "QuitScene": "0",
            "MemChangeCnt": "1", **overrides}


async def submit(app, payload):
    async with app.state.sessions() as db:
        await enqueue(db, app.state.settings, 1, payload)
    await app.state.worker.tick()


@pytest.mark.parametrize("first", ["failure", "stale"])
async def test_group_retry_keeps_original_baseline_and_recovers(system, first):
    app, client, _ = system
    old = [{"userid": "external", "type": "2", "name": "群中姓名"}, {"userid": "staff", "type": 1}]
    async with app.state.sessions() as db:
        db.add(Profile(tenant_id=1, kind="group", external_id="chat", synced_at=1,
                       data={"member_list": old}))
        await db.commit()
    calls = 0
    async def group(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            if first == "failure":
                raise PlatformError("platform_transport_error")
            return {"member_list": old}
        return {"name": "群", "member_list": old[1:]}
    app.state.platforms.group = group
    await submit(app, group_payload())
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        assert not row.snapshot["member_candidates"]
        job = await db.scalar(select(Job))
        assert job.status == "retry"
        job.next_run_at = 0
        await db.commit()
    await app.state.worker.tick()
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        assert row.snapshot["member_candidates"][0]["member_id"] == "external"
        assert row.snapshot["member_candidates"][0]["name"] == "群中姓名"
        assert row.snapshot["member_candidates"][0]["member_type"] == 2
        assert row.snapshot["reported_event_type"] == "group_left"
        assert row.enrichment_status == "complete"
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 1
        event_id = row.id
    await login(client)
    detail = (await client.get(f"/api/v1/events/{event_id}")).json()["data"]
    assert "_before_members" not in detail["snapshot"]


async def test_group_missing_name_is_enriched_by_id_and_type_does_not_invent_operator(system):
    app, _, _ = system
    async with app.state.sessions() as db:
        db.add(Profile(tenant_id=1, kind="group", external_id="chat", synced_at=1,
                       data={"member_list": [{"userid": "external", "type": 2}]}))
        await db.commit()
    async def group(*args):
        return {"member_list": []}
    async def customer(*args):
        return {"external_contact": {"name": "接口补全的姓名", "type": 1}}
    app.state.platforms.group, app.state.platforms.customer = group, customer
    await submit(app, group_payload(QuitScene="1"))
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        assert row.snapshot["member_candidates"][0]["name"] == "接口补全的姓名"
        assert row.snapshot["reported_event_type"] == "group_removed"
        assert row.operator_id is None


@pytest.mark.parametrize("change,direction,event_type", [
    ("add_external_contact", None, "contact_added"),
    ("del_external_contact", "employee_deleted_customer", "contact_deleted"),
    ("del_follow_user", "customer_deleted_employee", "contact_deleted"),
])
async def test_contact_identity_survives_profile_failure(system, change, direction, event_type):
    app, _, _ = system
    async def fail(*args):
        raise PlatformError("platform_transport_error")
    app.state.platforms.customer = app.state.platforms.employee = fail
    await submit(app, {"Event": "change_external_contact", "ChangeType": change,
                      "CreateTime": str(now_ms() // 1000), "UserID": "staff", "ExternalUserID": "external"})
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        assert row.event_type == event_type and row.deletion_direction == direction
        assert row.customer_id == "external" and row.resolution == "exact"
        assert row.snapshot["customer_name"] == "external"
        assert row.enrichment_status == "partial"


@pytest.mark.parametrize("count", ["", "0", "-1", "abc", None])
async def test_invalid_group_count_is_not_invented_as_one_person(system, count):
    app, _, _ = system
    async def group(*args):
        return {"member_list": []}
    app.state.platforms.group = group
    await submit(app, group_payload(MemChangeCnt=count))
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 0
        assert (await db.scalar(select(Job))).last_error == "group_member_count_invalid"


@pytest.mark.parametrize("change", ["create", "dismiss", "unsupported"])
async def test_non_update_does_not_become_member_departure(system, change):
    app, _, _ = system
    async def group(*args):
        return {"member_list": []}
    app.state.platforms.group = group
    await submit(app, group_payload(ChangeType=change))
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 0


async def test_missing_contact_id_is_not_marked_exact(system):
    app, _, _ = system
    await submit(app, {"Event": "change_external_contact", "ChangeType": "add_external_contact",
                      "CreateTime": str(now_ms() // 1000), "UserID": "staff"})
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 0
        assert (await db.scalar(select(Job))).last_error == "contact_identity_fields_missing"


async def test_avatar_io_failure_keeps_retrieved_person(system, monkeypatch):
    app, _, _ = system
    async def customer(*args):
        return {"external_contact": {"name": "已获取的姓名", "avatar": "https://qlogo.cn/avatar"}}
    async def avatar(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr("app.worker.save_avatar", avatar)
    app.state.platforms.customer = customer
    from app.models import Tenant
    async with app.state.sessions() as db:
        tenant = await db.get(Tenant, 1)
    data, complete = await app.state.worker.get_profile(tenant, "customer", "external")
    assert data["name"] == "已获取的姓名" and not complete


@pytest.mark.parametrize("scene", ["", "2", "unexpected"])
async def test_unknown_quit_reason_is_not_assumed_to_be_kick_or_leave(system, scene):
    app, _, _ = system
    async def group(*args):
        return {"member_list": []}
    app.state.platforms.group = group
    await submit(app, group_payload(QuitScene=scene))
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        assert row.snapshot["reported_event_type"] == "group_change_unknown"


@pytest.mark.parametrize("change", ["add_member", "change_owner", "change_name", "change_notice"])
async def test_non_departure_group_updates_refresh_baseline_without_loss_events(system, change):
    app, _, _ = system
    async def group(*args):
        return {"name": "新群名", "member_list": [{"userid": "external", "type": 2}]}
    app.state.platforms.group = group
    await submit(app, group_payload(UpdateDetail=change))
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 0
        profile = await db.scalar(select(Profile).where(Profile.kind == "group"))
        assert profile.data["member_list"][0]["userid"] == "external"


@pytest.mark.parametrize("event,change", [("subscribe", ""),
    ("change_external_contact", "add_half_external_contact"),
    ("change_external_contact", "transfer_fail"), ("change_external_contact", "unsupported")])
async def test_non_business_or_incomplete_relationship_does_not_count_as_loss(system, event, change):
    app, _, _ = system
    await submit(app, {"Event": event, "ChangeType": change, "CreateTime": str(now_ms() // 1000)})
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 0


async def test_edit_contact_refresh_failure_is_retried_not_silently_done(system):
    app, _, _ = system
    async def fail(*args):
        raise PlatformError("platform_transport_error")
    app.state.platforms.customer = fail
    await submit(app, {"Event": "change_external_contact", "ChangeType": "edit_external_contact",
                      "ExternalUserID": "external", "CreateTime": str(now_ms() // 1000)})
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 0
        assert (await db.scalar(select(Job))).status == "retry"
