import asyncio,json
from sqlalchemy import select
from app.config import Settings
from app.db import make_database
from app.models import BusinessEvent,Tenant
from app.platforms import Platforms
async def main():
 s=Settings();engine,sessions=make_database(s);p=Platforms(s)
 try:
  async with sessions() as db:
   rows=(await db.scalars(select(BusinessEvent))).all()
   tids={r.tenant_id for r in rows if not r.snapshot.get('demo')}
   tenants=(await db.scalars(select(Tenant).where(Tenant.id.in_(tids),Tenant.enabled.is_(True)))).all()
  for t in tenants:
   cursor='';count=0;external=0;internal=0
   while True:
    batch=await p.wecom(t,'externalcontact/groupchat/list','POST',body={'limit':100,'cursor':cursor})
    for g in batch.get('group_chat_list',[]):
     detail=await p.group(t,g['chat_id']);count+=1
     external+=sum(str(m.get('type'))=='2' for m in detail.get('member_list',[]))
     internal+=sum(str(m.get('type'))=='1' for m in detail.get('member_list',[]))
    cursor=batch.get('next_cursor','')
    if not cursor:break
   print(json.dumps({'live_groups':count,'current_external_memberships':external,'current_internal_memberships':internal}))
 finally:await p.close();await engine.dispose()
asyncio.run(main())
