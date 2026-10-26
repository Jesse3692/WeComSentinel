"""Proactive caching before departure; isolated DB and platform responses."""

import pytest
from sqlalchemy import select, func
from app.models import Tenant, BusinessEvent, Job, now_ms
from app.platforms import PlatformError
from app.worker import enqueue
import test_system

system = test_system.system


async def tenant(app, tid=1):
    async with app.state.sessions() as db:
        return await db.get(Tenant, tid)


@pytest.mark.parametrize("reason,event_type", [("0", "group_left"), ("1", "group_removed")])
async def test_sync_caches_real_member_identity_then_departure_uses_old_photo(
    system, monkeypatch, reason, event_type
):
    app, _, _ = system
    worker = app.state.worker

    async def customer(*args):
        return {
            "external_contact": {"name": "客户", "type": 1, "avatar": "http://wx.qlogo.cn/test"}
        }

    async def download(*args):
        return 12

    app.state.platforms.customer = customer
    monkeypatch.setattr("app.worker.save_avatar", download)
    t = await tenant(app)
    detail, complete = await worker.sync_group(
        t, "group", {"name": "群", "member_list": [{"userid": "member", "type": 2}]}, sampled_at=1
    )
    assert complete and detail["member_list"][0]["media_id"] == 12
    assert (await worker.cached(1, "customer", "member")).data["media_id"] == 12

    async def unavailable(*args):
        raise PlatformError("platform_error_84061")

    async def group(*args):
        return {"name": "群", "member_list": []}

    app.state.platforms.customer = unavailable
    app.state.platforms.group = group
    async with app.state.sessions() as db:
        await enqueue(
            db,
            app.state.settings,
            1,
            {
                "Event": "change_external_chat",
                "ChangeType": "update",
                "UpdateDetail": "del_member",
                "QuitScene": reason,
                "MemChangeCnt": "1",
                "ChatId": "group",
                "CreateTime": str(now_ms() // 1000),
            },
        )
    await worker.tick()
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        assert row.event_type == event_type and row.quantity == 1 and row.resolution == "inferred"
        assert row.operator_id is None
        assert row.snapshot["member_candidates"][0]["media_id"] == 12
        assert (await db.scalar(select(Job))).status == "done"


@pytest.mark.parametrize("change,detail", [("create", None), ("update", "add_member")])
async def test_create_and_join_callbacks_prefetch_without_business_events(
    system, monkeypatch, change, detail
):
    app, _, _ = system

    async def group(*args):
        return {"name": "群", "member_list": [{"userid": "external", "type": 2}]}

    async def customer(*args):
        return {"external_contact": {"name": "客户", "avatar": "http://wx.qlogo.cn/test"}}

    async def download(*args):
        return 15

    app.state.platforms.group, app.state.platforms.customer = group, customer
    monkeypatch.setattr("app.worker.save_avatar", download)
    async with app.state.sessions() as db:
        await enqueue(
            db,
            app.state.settings,
            1,
            {
                "Event": "change_external_chat",
                "ChangeType": change,
                "UpdateDetail": detail,
                "ChatId": "group",
                "CreateTime": str(now_ms() // 1000),
            },
        )
    await app.state.worker.tick()
    assert (await app.state.worker.cached(1, "customer", "external")).data["media_id"] == 15
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 0
        assert (await db.scalar(select(Job))).status == "done"


async def test_prefetch_failure_retains_membership_and_job_retries(system, monkeypatch):
    app, _, _ = system
    available = False

    async def group(*args):
        return {"name": "群", "member_list": [{"userid": "external", "type": 2}]}

    async def customer(*args):
        if not available:
            raise PlatformError("platform_transport_error")
        return {"external_contact": {"name": "客户", "avatar": "http://wx.qlogo.cn/test"}}

    async def download(*args):
        return 22

    app.state.platforms.group, app.state.platforms.customer = group, customer
    monkeypatch.setattr("app.worker.save_avatar", download)
    async with app.state.sessions() as db:
        await enqueue(
            db,
            app.state.settings,
            1,
            {
                "Event": "change_external_chat",
                "ChangeType": "update",
                "UpdateDetail": "add_member",
                "ChatId": "group",
                "CreateTime": str(now_ms() // 1000),
            },
        )
    await app.state.worker.tick()
    assert len((await app.state.worker.cached(1, "group", "group")).data["member_list"]) == 1
    async with app.state.sessions() as db:
        job = await db.scalar(select(Job))
        assert job.status == "retry"
        job.next_run_at = 0
        await db.commit()
    available = True
    await app.state.worker.tick()
    assert (await app.state.worker.cached(1, "group", "group")).data["member_list"][0][
        "media_id"
    ] == 22
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 0
        assert (await db.scalar(select(Job))).status == "done"


async def test_group_snapshot_photo_survives_profile_loss_and_cross_tenant(system, monkeypatch):
    app, _, _ = system
    worker = app.state.worker
    await worker.put_profile(2, "customer", "same-id", {"name": "其他主体", "media_id": 99})
    from types import SimpleNamespace

    previous = SimpleNamespace(
        synced_at=1,
        data={
            "name": "群",
            "member_list": [{"userid": "same-id", "type": 2, "name": "客户", "media_id": 23}],
        },
    )

    async def should_not_query(*args):
        raise AssertionError("history photo should be sufficient")

    monkeypatch.setattr(worker, "get_profile", should_not_query)
    snapshot = await worker.group_snapshot(
        await tenant(app),
        SimpleNamespace(id=-1),
        {"CreateTime": str(now_ms() // 1000), "MemChangeCnt": "1", "QuitScene": "1"},
        previous,
        {"member_list": []},
    )
    assert snapshot["member_candidates"][0]["media_id"] == 23


async def test_slow_prefetch_keeps_capture_time_and_does_not_overwrite_newer_group(
    system, monkeypatch
):
    app, _, _ = system
    worker = app.state.worker

    async def profile(*args):
        await worker.put_profile(
            1, "group", "group", {"name": "新群快照", "member_list": []}, synced_at=200
        )
        return {"name": "客户", "media_id": 24}, True

    monkeypatch.setattr(worker, "get_profile", profile)
    await worker.sync_group(
        await tenant(app),
        "group",
        {"name": "旧群快照", "member_list": [{"userid": "a", "type": 2}]},
        sampled_at=100,
    )
    cached = await worker.cached(1, "group", "group")
    assert cached.synced_at == 200 and cached.data["member_list"] == []


async def test_cli_sync_includes_group_only_customer(system, monkeypatch):
    from app.cli import sync_profiles
    from types import SimpleNamespace

    app, _, _ = system

    class Platform:
        async def wecom(self, tenant, path, *args, **kwargs):
            if path == "externalcontact/get_follow_user_list":
                return {"follow_user": []}
            assert path == "externalcontact/groupchat/list"
            return {"group_chat_list": [{"chat_id": "group"}]}

        async def group(self, *args):
            return {"name": "群", "member_list": [{"userid": "group-only", "type": 2}]}

        async def customer(self, *args):
            return {"external_contact": {"name": "客户", "avatar": "http://wx.qlogo.cn/test"}}

        async def close(self):
            pass

    async def download(*args):
        return 31

    monkeypatch.setattr("app.cli.Platforms", lambda settings: Platform())
    monkeypatch.setattr(
        "app.cli.make_database", lambda settings: (app.state.engine, app.state.sessions)
    )
    monkeypatch.setattr("app.worker.save_avatar", download)
    await sync_profiles(app.state.settings, SimpleNamespace(tenant_id=1))
    assert (await app.state.worker.cached(1, "customer", "group-only")).data["media_id"] == 31
    assert (await app.state.worker.cached(1, "group", "group")).data["member_list"][0][
        "media_id"
    ] == 31
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 0


async def test_prefetch_limits_parallel_requests_and_preserves_sample_time(system, monkeypatch):
    import asyncio

    app, _, _ = system
    active = peak = 0

    async def profile(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"name": "客户", "media_id": 33}, True

    monkeypatch.setattr(app.state.worker, "get_profile", profile)
    detail, complete = await app.state.worker.sync_group(
        await tenant(app),
        "group",
        {"member_list": [{"userid": f"m{i}", "type": 2} for i in range(8)]},
        sampled_at=100,
    )
    assert complete and peak == 3
    assert all(m["media_id"] == 33 for m in detail["member_list"])
    assert (await app.state.worker.cached(1, "group", "group")).synced_at == 100
