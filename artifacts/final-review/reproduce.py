import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tests'))
import test_system
from app.models import Tenant, Inbox, BusinessEvent
from sqlalchemy import select
system=test_system.system

async def test_reproduce_avatar_retry_stuck_with_old_media(system):
    app,_,_=system
    worker=app.state.worker
    calls=0
    async def profile(*args):
        nonlocal calls
        calls+=1
        return {'name':'客户','media_id':42},False
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
    assert calls==1
    assert second['member_candidates'][0]['avatar_state']=='retry'
