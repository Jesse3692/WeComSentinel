"""Real-database regressions for capture ordering and bounded job failures."""

import asyncio
from types import SimpleNamespace

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import func, select

from app.models import Inbox, Job, Profile, Tenant
from app.platforms import PlatformError
from app.worker import Worker, enqueue
import test_system

system = test_system.system


async def tenant(app):
    async with app.state.sessions() as db:
        return await db.get(Tenant, 1)


@pytest.mark.parametrize("entrypoint", ["sync", "callback"])
async def test_group_request_order_beats_response_order_in_same_millisecond(
    system, monkeypatch, entrypoint
):
    app, _, _ = system
    worker = app.state.worker
    group_tenant = await tenant(app)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def group(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            return {"name": "旧响应", "member_list": []}
        return {"name": "新响应", "member_list": []}

    monkeypatch.setattr(app.state.platforms, "group", group)
    monkeypatch.setattr("app.worker.now_ms", lambda: 1000)

    async def capture():
        if entrypoint == "sync":
            return await worker.sync_group(group_tenant, "group")
        return await worker.normalize(
            group_tenant,
            SimpleNamespace(id=-1),
            {
                "Event": "change_external_chat",
                "ChangeType": "create",
                "ChatId": "group",
                "CreateTime": "1",
            },
        )

    earlier = asyncio.create_task(capture())
    await started.wait()
    try:
        await capture()
    finally:
        release.set()
        await earlier
    saved = await worker.cached(1, "group", "group")
    assert saved.name == "新响应" and saved.capture_version == 2
    assert saved.synced_at == 1000


async def test_capture_reservation_keeps_completed_snapshot_and_baseline_time(system, monkeypatch):
    app, _, _ = system
    worker = app.state.worker
    group_tenant = await tenant(app)
    await worker.put_profile(
        1,
        "group",
        "group",
        {"name": "历史快照", "member_list": [{"userid": "customer", "type": 2}]},
        synced_at=500,
    )
    assert await worker.reserve_group_capture(1, "group") == 1
    historical = await worker.cached(1, "group", "group")
    assert historical.synced_at == 500 and historical.name == "历史快照"
    assert await worker.reserve_group_capture(1, "empty") == 1
    placeholder = await worker.cached(1, "group", "empty")
    assert placeholder.synced_at == 0 and placeholder.data == {}

    clock = 1000

    async def group(*args):
        nonlocal clock
        clock = 3000  # The response crosses a callback that occurred at 2000.
        return {"name": "事后快照", "member_list": [{"userid": "later", "type": 2}]}

    monkeypatch.setattr("app.worker.now_ms", lambda: clock)
    monkeypatch.setattr(app.state.platforms, "group", group)

    async def profile(*args):
        return {"name": "客户"}, True

    monkeypatch.setattr(worker, "get_profile", profile)
    await worker.sync_group(group_tenant, "group")
    saved = await worker.cached(1, "group", "group")
    assert saved.synced_at == 3000
    snapshot = await worker.group_snapshot(
        group_tenant,
        SimpleNamespace(id=-1),
        {"CreateTime": "2", "MemChangeCnt": "1", "QuitScene": "0"},
        saved,
        {"member_list": []},
    )
    assert snapshot["_before_members"] is None
    assert snapshot["member_evidence"] == "no_before_snapshot"


async def test_old_prefetch_final_write_cannot_replace_new_capture_in_same_millisecond(
    system, monkeypatch
):
    app, _, _ = system
    worker = app.state.worker
    group_tenant = await tenant(app)
    prefetch_started, release_prefetch = asyncio.Event(), asyncio.Event()
    calls = 0

    async def group(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"name": "旧名单", "member_list": [{"userid": "customer", "type": 2}]}
        return {"name": "新名单", "member_list": []}

    async def profile(*args):
        prefetch_started.set()
        await release_prefetch.wait()
        return {"name": "客户", "media_id": 42}, True

    monkeypatch.setattr(app.state.platforms, "group", group)
    monkeypatch.setattr(worker, "get_profile", profile)
    monkeypatch.setattr("app.worker.now_ms", lambda: 1000)
    earlier = asyncio.create_task(worker.sync_group(group_tenant, "group"))
    await prefetch_started.wait()
    try:
        await worker.sync_group(group_tenant, "group")
    finally:
        release_prefetch.set()
        await earlier
    saved = await worker.cached(1, "group", "group")
    assert saved.name == "新名单" and saved.data["member_list"] == []
    assert saved.capture_version == 2 and saved.synced_at == 1000


async def test_failed_new_capture_does_not_allow_an_earlier_response_to_replace_history(
    system, monkeypatch
):
    app, _, _ = system
    worker = app.state.worker
    group_tenant = await tenant(app)
    await worker.put_profile(1, "group", "group", {"name": "历史快照"}, synced_at=1)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def group(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            return {"name": "迟到响应", "member_list": []}
        raise PlatformError("platform_transport_error")

    monkeypatch.setattr(app.state.platforms, "group", group)
    earlier = asyncio.create_task(worker.sync_group(group_tenant, "group"))
    await started.wait()
    try:
        with pytest.raises(PlatformError):
            await worker.sync_group(group_tenant, "group")
    finally:
        release.set()
        await earlier
    saved = await worker.cached(1, "group", "group")
    assert saved.name == "历史快照" and saved.synced_at == 1


async def test_concurrent_profile_creation_uses_one_identity_row(system):
    app, _, _ = system
    worker = app.state.worker
    await asyncio.gather(
        *(worker.put_profile(1, "customer", "same", {"name": f"客户{i}"}) for i in range(8))
    )
    async with app.state.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Profile)
                .where(
                    Profile.tenant_id == 1,
                    Profile.kind == "customer",
                    Profile.external_id == "same",
                )
            )
            == 1
        )
    await worker.put_profile(2, "customer", "same", {"name": "另一个主体"})
    assert (await worker.cached(2, "customer", "same")).name == "另一个主体"


@pytest.mark.parametrize(
    "broken,error",
    [(b"invalid-ciphertext", "InvalidToken"), (b"{private-invalid-json", "JSONDecodeError")],
)
async def test_bad_payload_converges_and_does_not_block_later_jobs(system, broken, error):
    app, _, _ = system
    encrypted = (
        broken
        if error == "InvalidToken"
        else Fernet(app.state.settings.payload_key.encode()).encrypt(broken)
    )
    async with app.state.sessions() as db:
        inbox = Inbox(tenant_id=1, dedupe_key="broken", payload_encrypted=encrypted)
        db.add(inbox)
        await db.flush()
        db.add(Job(tenant_id=1, callback_id=inbox.id))
        await db.commit()
        inbox_id = inbox.id
        later = await enqueue(db, app.state.settings, 1, {"Event": "ignored", "CreateTime": "1"})
    assert await app.state.worker.tick()
    assert await app.state.worker.tick()
    async with app.state.sessions() as db:
        assert (await db.scalar(select(Job).where(Job.callback_id == later))).status == "done"
    for attempt in range(2, 6):
        async with app.state.sessions() as db:
            job = await db.scalar(select(Job).where(Job.callback_id == inbox_id))
            assert job.status == "retry" and job.lease_until is None
            assert job.last_error == error
            job.next_run_at = 0
            await db.commit()
        assert await app.state.worker.tick()
    async with app.state.sessions() as db:
        job = await db.scalar(select(Job).where(Job.callback_id == inbox_id))
        assert job.status == "failed" and job.attempts == 5 and job.lease_until is None
        assert job.last_error == error
        assert (await db.get(Inbox, inbox_id)).status == "failed"
    assert not await app.state.worker.tick()


async def test_preprocessing_failure_uses_sanitized_retry_state(system, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    app, _, _ = system
    async with app.state.sessions() as db:
        await enqueue(db, app.state.settings, 1, {"Event": "ignored", "CreateTime": "1"})
    original_get = AsyncSession.get

    async def get(self, entity, identity, *args, **kwargs):
        if entity is Tenant:
            raise ValueError("private-request-url-and-personal-data")
        return await original_get(self, entity, identity, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", get)
    assert await app.state.worker.tick()
    async with app.state.sessions() as db:
        job = await db.scalar(select(Job))
        assert job.status == "retry" and job.last_error == "ValueError"
        assert job.lease_until is None


async def test_cancelled_execution_retains_lease_and_recovers(system, monkeypatch):
    app, _, _ = system
    async with app.state.sessions() as db:
        await enqueue(db, app.state.settings, 1, {"Event": "ignored", "CreateTime": "1"})
    started = asyncio.Event()

    async def pause(*args):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(app.state.worker, "normalize", pause)
    task = asyncio.create_task(app.state.worker.tick())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with app.state.sessions() as db:
        job = await db.scalar(select(Job))
        assert job.status == "running" and job.lease_until is not None
        job.lease_until = 0
        await db.commit()

    async def complete(*args):
        return True

    monkeypatch.setattr(app.state.worker, "normalize", complete)
    assert await app.state.worker.tick()
    async with app.state.sessions() as db:
        job = await db.scalar(select(Job))
        assert job.status == "done" and job.attempts == 2


async def test_old_lease_completion_cannot_overwrite_reclaimed_job(system, monkeypatch):
    app, _, _ = system
    async with app.state.sessions() as db:
        await enqueue(db, app.state.settings, 1, {"Event": "ignored", "CreateTime": "1"})
    old_worker = app.state.worker
    new_worker = Worker(app.state.settings, app.state.sessions, app.state.platforms)
    started, release = asyncio.Event(), asyncio.Event()

    async def old_normalize(*args):
        started.set()
        await release.wait()
        return True

    async def new_normalize(*args):
        raise PlatformError("platform_transport_error")

    monkeypatch.setattr(old_worker, "normalize", old_normalize)
    monkeypatch.setattr(new_worker, "normalize", new_normalize)
    old_task = asyncio.create_task(old_worker.tick())
    await started.wait()
    try:
        async with app.state.sessions() as db:
            job = await db.scalar(select(Job))
            job.lease_until = 0
            await db.commit()
        await new_worker.tick()
    finally:
        release.set()
        await old_task
    async with app.state.sessions() as db:
        job = await db.scalar(select(Job))
        assert job.status == "retry" and job.attempts == 2
        assert job.last_error == "platform_transport_error"
        assert (await db.get(Inbox, job.callback_id)).status == "retry"


async def test_repeated_crashes_stop_after_processing_budget(system, monkeypatch):
    app, _, _ = system
    async with app.state.sessions() as db:
        await enqueue(db, app.state.settings, 1, {"Event": "ignored", "CreateTime": "1"})
        job = await db.scalar(select(Job))
        job.status, job.attempts, job.lease_until = "running", 5, 0
        await db.commit()

    async def must_not_process(*args):
        raise AssertionError("The sixth claim only settles exhausted work")

    monkeypatch.setattr(app.state.worker, "normalize", must_not_process)
    assert await app.state.worker.tick()
    async with app.state.sessions() as db:
        job = await db.scalar(select(Job))
        assert job.status == "failed" and job.last_error == "processing_attempts_exhausted"
