import pytest
import httpx
from app.media import normalize_avatar_url, save_avatar
import test_system
system=test_system.system

@pytest.mark.parametrize('url,expected',[
 ('http://wx.qlogo.cn/path?a=1','https://wx.qlogo.cn/path?a=1'),
 ('http://wework.qpic.cn:80/a','https://wework.qpic.cn/a'),
 ('https://wx.qlogo.cn:443/a','https://wx.qlogo.cn/a'),
 ('https://wx.qlogo.cn.evil.example/a',None),('http://localhost/a',None),
 ('http://127.0.0.1/a',None),('http://wx.qlogo.cn:8080/a',None),
 ('https://wx.qlogo.cn:80/a',None),('http://u:p@wx.qlogo.cn/a',None),
 ('ftp://wx.qlogo.cn/a',None),('http://wx.qlogo.cn:invalid/a',None),
 ('http://wx.qlogo.cn/\nabc',None),('http://wx.qlogo.cn\\@evil.example/a',None),
])
def test_normalize(url,expected):
    assert normalize_avatar_url(url)==expected

async def test_http_is_upgraded_before_download_and_redirect_is_rejected(system, monkeypatch):
    app,_,_=system
    calls=[]
    real=httpx.AsyncClient
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False
        return real(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr('app.media.httpx.AsyncClient',client)
    assert await save_avatar(app.state.settings,app.state.sessions,1,'http://wx.qlogo.cn/avatar') is None
    assert calls==['https://wx.qlogo.cn/avatar']

async def test_backfill_changes_only_avatar_fields(system,monkeypatch,tmp_path):
    from scripts.backfill_avatars import run
    from test_system import insert_event
    from app.models import BusinessEvent
    from app.worker import Worker
    from copy import deepcopy
    app,_,_=system
    ident=await insert_event(app)
    async with app.state.sessions() as db:
        row=await db.get(BusinessEvent,ident)
        row.customer_id='customer'
        row.snapshot={**row.snapshot,'demo':False}
        await db.commit()
        before={k:deepcopy(getattr(row,k)) for k in ('event_type','quantity','resolution','occurred_at','add_way','deletion_direction','snapshot')}
    async def profile(self,tenant,kind,identity,force=False):
        assert force
        return {'media_id':42 if kind=='customer' else 43},True
    monkeypatch.setattr(Worker,'get_profile',profile)
    monkeypatch.chdir(tmp_path)
    result=await run(app.state.settings,True)
    assert result['patched_records']==1 and result['backup_created']
    async with app.state.sessions() as db:
        row=await db.get(BusinessEvent,ident)
        assert row.snapshot['customer_media_id']==42
        assert row.snapshot['employee_media_id']==43
        assert {k:v for k,v in row.snapshot.items() if k not in ('customer_media_id','employee_media_id')}==before['snapshot']
        assert all(getattr(row,k)==value for k,value in before.items() if k!='snapshot')


async def test_avatar_retry_with_retained_old_media_recovers(system):
    from app.models import Tenant, Inbox, BusinessEvent
    app,_,_=system
    worker=app.state.worker
    calls=0
    async def profile(*args):
        nonlocal calls
        calls+=1
        return {'name':'客户','media_id':42 if calls==1 else 43},calls>1
    worker.get_profile=profile
    async with app.state.sessions() as db:
        tenant=await db.get(Tenant,1)
        inbox=Inbox(tenant_id=1,dedupe_key='retry-case',payload_encrypted=b'fixture')
        db.add(inbox)
        await db.commit()
        callback_id=inbox.id
    from types import SimpleNamespace
    previous=SimpleNamespace(synced_at=1,data={'name':'群','member_list':[{'userid':'member','type':2,'name':'客户'}]})
    payload={'CreateTime':'1000','MemChangeCnt':'1','QuitScene':'0'}
    first=await worker.group_snapshot(tenant,inbox,payload,previous,{'name':'群','member_list':[]})
    assert first['member_candidates'][0]['avatar_state']=='retry'
    async with app.state.sessions() as db:
        db.add(BusinessEvent(tenant_id=1,callback_id=callback_id,item_key='group',event_type='group_left',occurred_at=1000000,quantity=1,snapshot=first))
        await db.commit()
    second=await worker.group_snapshot(tenant,inbox,payload,None,{'name':'群','member_list':[]})
    assert calls==2
    assert second['member_candidates'][0]['avatar_state']=='stored'
    assert second['member_candidates'][0]['media_id']==43

async def test_departed_contact_downloads_cached_http_avatar(system,monkeypatch):
    from app.models import Tenant
    from app.platforms import PlatformError
    app,_,_=system
    worker=app.state.worker
    await worker.put_profile(1,'customer','departed',{'name':'历史客户','avatar':'http://wx.qlogo.cn/avatar'})
    async def removed(*args):
        raise PlatformError('customer_unavailable')
    async def download(*args):
        assert args[-1]=='http://wx.qlogo.cn/avatar'
        return 44
    app.state.platforms.customer=removed
    monkeypatch.setattr('app.worker.save_avatar',download)
    async with app.state.sessions() as db:
        tenant=await db.get(Tenant,1)
    data,complete=await worker.get_profile(tenant,'customer','departed',force=True)
    assert data['media_id']==44 and data['refresh_failed'] and not complete
    assert (await worker.cached(1,'customer','departed')).data['media_id']==44
