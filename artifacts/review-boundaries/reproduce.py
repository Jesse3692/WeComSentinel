import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
import test_system
from test_system import login
import pytest
from app.models import Tenant
system = test_system.system

async def test_reproduce_cached_avatar_lost_on_refresh(system, monkeypatch):
    app, _, _ = system
    worker = app.state.worker
    await worker.put_profile(1, 'customer', 'customer', {'name':'客户','avatar':'https://example.invalid/old','media_id':42})
    async def customer(*args):
        return {'external_contact':{'name':'客户','avatar':'https://example.invalid/old'}}
    async def failed_download(*args):
        return None
    app.state.platforms.customer = customer
    monkeypatch.setattr('app.worker.save_avatar', failed_download)
    async with app.state.sessions() as db:
        tenant = await db.get(Tenant,1)
    data, _ = await worker.get_profile(tenant,'customer','customer',force=True)
    assert data.get('media_id') is None  # Reproduces loss of the previously saved 42.

async def test_reproduce_member_avatar_overwritten(system):
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
    assert snapshot['member_candidates'][0]['media_id'] is None  # 42 overwritten in customer update.

async def test_reproduce_end_only_ranges_differ(system):
    _, client, _ = system
    await login(client)
    data=(await client.get('/api/v1/dashboard?end_date=2025-12-31')).json()['data']
    assert data['metrics_range']==['2025-12-25','2025-12-31']
    assert data['trend_range']==['2025-12-02','2025-12-31']
