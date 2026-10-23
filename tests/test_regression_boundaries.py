"""Regression coverage with isolated SQLite and platform substitutes."""
from datetime import datetime as real_datetime
from zoneinfo import ZoneInfo

import pytest
from types import SimpleNamespace
from app.models import Tenant
from sqlalchemy import select

import app.api as api_module
from app.models import BusinessEvent, Job, Profile, now_ms
from app.platforms import PlatformError
from app.worker import enqueue
import test_system
from test_system import insert_event, login

system = test_system.system


async def test_group_search_effective_name_and_tenant(system):
    app, client, _ = system
    event = await insert_event(app, kind="group_left")
    async with app.state.sessions() as db:
        row = await db.get(BusinessEvent, event)
        row.snapshot = {"group_name": ""}
        db.add_all([Profile(tenant_id=t, kind="group", external_id="chat1", name=name,
                           data={"name": name}) for t, name in [(1, "当前可见群"), (2, "其他主体群")]])
        await db.commit()
    await login(client, "user")
    for name, expected in [("当前可见群", 1), ("其他主体群", 0)]:
        data = (await client.get('/api/v1/events', params={"group_keyword": name})).json()['data']
        assert data['total_records'] == expected
    async with app.state.sessions() as db:
        row = await db.get(BusinessEvent, event)
        row.snapshot = {"group_name": "历史群"}
        await db.commit()
    assert (await client.get('/api/v1/events?group_keyword=当前可见群')).json()['data']['total_records'] == 0
    assert (await client.get('/api/v1/events?group_keyword=历史群')).json()['data']['total_records'] == 1


async def test_employee_options_fallback_search_and_pagination(system):
    app, client, _ = system
    await insert_event(app)
    await insert_event(app, tenant=2)
    async with app.state.sessions() as db:
        db.add_all([Profile(tenant_id=1, kind="employee", external_id=f"staff{i:04}",
                           name=f"员工{i:04}", data={}) for i in range(505)])
        await db.commit()
    await login(client, 'user')
    rows = []
    for offset in range(0, 600, 100):
        rows += (await client.get('/api/v1/employees', params={'offset': offset})).json()['data']
    assert len(rows) == len({(r['tenant_id'], r['id']) for r in rows}) == 506
    assert all(r['tenant_id'] == 1 for r in rows)
    assert {'id': 'employee1', 'tenant_id': 1, 'name': 'employee1'} in rows
    assert (await client.get('/api/v1/employees?keyword=staff0504')).json()['data'][0]['id'] == 'staff0504'


async def test_effective_dates_and_midnight_pagination(system, monkeypatch):
    app, client, _ = system
    class Clock(real_datetime):
        day = 31
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 12, 31, 23, 59, tzinfo=ZoneInfo('Asia/Shanghai')) if cls.day == 31 else cls(2026, 1, 1, 0, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
    monkeypatch.setattr(api_module, 'datetime', Clock)
    stamp = int(Clock.now().timestamp()*1000)
    for i in range(3):
        await insert_event(app, stamp=stamp-i*1000)
    await login(client)
    first = (await client.get('/api/v1/events?limit=1')).json()['data']
    assert first['range'] == ['2025-12-25', '2025-12-31']
    Clock.day = 1
    second = (await client.get('/api/v1/events', params={'limit': 1, 'cursor': first['next_cursor'],
        'start_date': first['range'][0], 'end_date': first['range'][1]})).json()['data']
    assert first['items'][0]['id'] != second['items'][0]['id']
    assert (await client.get('/api/v1/events')).json()['data']['range'] == ['2025-12-26', '2026-01-01']
    assert (await client.get('/api/v1/events?start_date=2025-12-30')).json()['data']['range'] == ['2025-12-30', '2026-01-01']


@pytest.mark.parametrize('relation', [[], [{'userid':'employee1','add_way':5}]])
async def test_add_refreshes_cached_relationship_and_retries_missing(system, relation):
    app, _, _ = system
    await app.state.worker.put_profile(1, 'customer', 'customer1', {'name':'客户', 'follow_user':[{'userid':'employee1','add_way':1}]})
    async def employee(*args):
        return {'name':'员工'}
    async def customer(*args):
        return {'external_contact':{'name':'客户'}, 'follow_user':relation}
    app.state.platforms.employee, app.state.platforms.customer = employee, customer
    async with app.state.sessions() as db:
        await enqueue(db, app.state.settings, 1, {'Event':'change_external_contact','ChangeType':'add_external_contact',
            'CreateTime':str(now_ms()//1000),'UserID':'employee1','ExternalUserID':'customer1'})
    await app.state.worker.tick()
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        job = await db.scalar(select(Job))
        assert row.add_way == ('5' if relation else None)
        assert row.enrichment_status == ('complete' if relation else 'partial')
        assert job.status == ('done' if relation else 'retry')


async def test_named_group_members_retry_avatar_and_keep_identity(system, monkeypatch):
    app, _, _ = system
    await app.state.worker.put_profile(1, 'group', 'chat', {'name':'群','member_list':[
        {'userid':'a','type':2,'name':'甲'}, {'userid':'b','type':2,'name':'乙'}]})
    async with app.state.sessions() as db:
        row = await db.scalar(select(Profile).where(Profile.kind=='group'))
        row.synced_at=1
        await db.commit()
    async def group(*args):
        return {'name':'群','member_list':[]}
    async def customer(tenant, identity):
        return {'external_contact':{'name':identity, 'avatar':'https://example.invalid/avatar'}}
    failed = True
    async def avatar(*args):
        if failed:
            raise PlatformError('avatar_unavailable')
        return 123
    monkeypatch.setattr('app.worker.save_avatar', avatar)
    app.state.platforms.group, app.state.platforms.customer = group, customer
    async with app.state.sessions() as db:
        await enqueue(db, app.state.settings, 1, {'Event':'change_external_chat','ChangeType':'update',
            'UpdateDetail':'del_member','ChatId':'chat','QuitScene':'1','MemChangeCnt':'2','CreateTime':str(now_ms()//1000)})
    await app.state.worker.tick()
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        assert row.quantity == 2 and row.event_type == 'group_removed'
        assert row.enrichment_status == 'partial'
        assert all(m['avatar_state']=='retry' for m in row.snapshot['member_candidates'])
        original_id = row.id
        job = await db.scalar(select(Job))
        job.next_run_at=0
        await db.commit()
    failed = False
    await app.state.worker.tick()
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        assert row.id == original_id and row.quantity == 2 and row.resolution == 'inferred'
        assert row.enrichment_status == 'complete'
        assert all(m['media_id']==123 for m in row.snapshot['member_candidates'])
        assert (await db.scalar(select(Job))).status == 'done'

async def test_new_employee_relationship_is_refetched_then_recovers(system):
    app, _, _ = system
    await app.state.worker.put_profile(1, 'customer', 'customer1', {
        'name': '客户', 'follow_user': [{'userid': 'employeeA', 'add_way': 1}]})
    relations = []
    async def employee(*args):
        return {'name': '员工B'}
    async def customer(*args):
        return {'external_contact': {'name': '客户'}, 'follow_user': relations}
    app.state.platforms.employee, app.state.platforms.customer = employee, customer
    async with app.state.sessions() as db:
        await enqueue(db, app.state.settings, 1, {'Event': 'change_external_contact',
            'ChangeType': 'add_external_contact', 'CreateTime': str(now_ms()//1000),
            'UserID': 'employeeB', 'ExternalUserID': 'customer1'})
    await app.state.worker.tick()
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        assert row.add_way is None and row.enrichment_status == 'partial'
        job = await db.scalar(select(Job))
        job.next_run_at = 0
        await db.commit()
    relations.append({'userid': 'employeeB', 'add_way': 3})
    await app.state.worker.tick()
    async with app.state.sessions() as db:
        row = await db.scalar(select(BusinessEvent))
        assert row.add_way == '3' and row.enrichment_status == 'complete'
        assert (await db.scalar(select(Job))).status == 'done'


async def test_force_failure_does_not_claim_cached_relation_is_current(system):
    app, _, _ = system
    from app.models import Tenant
    await app.state.worker.put_profile(1, 'customer', 'customer1', {
        'name': '客户', 'follow_user': [{'userid': 'employee1', 'add_way': 1}]})
    async def fail(*args):
        raise PlatformError('platform_transport_error')
    app.state.platforms.customer = fail
    async with app.state.sessions() as db:
        tenant = await db.get(Tenant, 1)
    data, complete = await app.state.worker.get_profile(tenant, 'customer', 'customer1', force=True)
    assert not complete and data['refresh_failed']


async def test_existing_member_media_is_preserved_without_platform_fetch(system):
    app, _, _ = system
    from app.models import Tenant, Inbox
    await app.state.worker.put_profile(1, 'customer', 'member', {'name': '客户', 'media_id': 42})
    async def unexpected(*args):
        raise AssertionError('already complete profile should not be fetched')
    app.state.platforms.customer = unexpected
    async with app.state.sessions() as db:
        tenant = await db.get(Tenant, 1)
        previous = Profile(data={'name':'群', 'member_list':[{'userid':'member','type':2}]}, synced_at=1)
        snapshot = await app.state.worker.group_snapshot(tenant, Inbox(id=-1),
            {'CreateTime':str(now_ms()//1000),'QuitScene':'0','MemChangeCnt':'1'}, previous,
            {'name':'群','member_list':[]})
    assert snapshot['member_candidates'][0]['media_id'] == 42
    assert snapshot['member_evidence'] == 'snapshot_difference'


async def test_fixed_cached_avatar_lost_on_refresh(system, monkeypatch):
    app, _, _ = system
    worker = app.state.worker
    await worker.put_profile(1, 'customer', 'customer', {'name':'客户','avatar':'https://example.invalid/old','media_id':42})
    async def customer(*args):
        return {'external_contact':{'name':'客户','avatar':'https://example.invalid/new'}}
    async def failed_download(*args):
        return None
    app.state.platforms.customer = customer
    monkeypatch.setattr('app.worker.save_avatar', failed_download)
    async with app.state.sessions() as db:
        tenant = await db.get(Tenant,1)
    data, _ = await worker.get_profile(tenant,'customer','customer',force=True)
    assert data['media_id'] == 42
    assert data['avatar_refresh_pending']
    cached = await worker.cached(1, 'customer', 'customer')
    assert cached.data['media_id'] == 42
    async def successful_download(*args):
        return 43
    monkeypatch.setattr('app.worker.save_avatar', successful_download)
    recovered, complete = await worker.get_profile(tenant,'customer','customer')
    assert complete and recovered['media_id'] == 43
    assert not recovered.get('avatar_refresh_pending')

async def test_fixed_member_avatar_overwritten(system):
    app, _, _ = system
    worker = app.state.worker
    async def missing(*args):
        return {'sync_error':'profile_unavailable'}, False
    worker.get_profile = missing
    async def cached(*args):
        return SimpleNamespace(data={'media_id':42}, name='未知')
    worker.cached = cached
    previous = SimpleNamespace(data={'name':'群','member_list':[{'userid':'member','type':2}]},synced_at=1)
    snapshot = await worker.group_snapshot(SimpleNamespace(id=1), SimpleNamespace(id=-1),
        {'CreateTime':'1000','MemChangeCnt':'1'},previous,{'member_list':[]})
    assert snapshot['member_candidates'][0]['media_id'] == 42
    assert snapshot['member_candidates'][0]['avatar_state'] == 'retry'

async def test_fixed_end_only_ranges_differ(system):
    _, client, _ = system
    await login(client)
    data=(await client.get('/api/v1/dashboard?end_date=2025-12-31')).json()['data']
    assert data['metrics_range']==['2025-12-25','2025-12-31']
    assert data['trend_range']==data['metrics_range']
    events=(await client.get('/api/v1/events?end_date=2025-12-31')).json()['data']
    assert events['range']==data['metrics_range']
