import asyncio,json,hashlib
from sqlalchemy import select
from pathlib import Path
from app.config import Settings
from app.db import make_database
from app.models import BusinessEvent,Tenant,Profile,Media
from app.platforms import Platforms
from app.worker import Worker
async def main():
 s=Settings();engine,sessions=make_database(s);p=Platforms(s);w=Worker(s,sessions,p)
 try:
  async with sessions() as db:
   rows=(await db.scalars(select(BusinessEvent))).all()
   tids={r.tenant_id for r in rows if not r.snapshot.get('demo')}
   tenants=(await db.scalars(select(Tenant).where(Tenant.id.in_(tids),Tenant.enabled.is_(True)))).all()
   before={r.id:(r.event_type,r.quantity,r.resolution,r.occurred_at,r.customer_id,r.employee_id,r.group_id,r.operator_id,r.deletion_direction,r.add_way,r.snapshot) for r in rows}
  stats={'synced_groups':0,'external_memberships':0,'internal_memberships':0,'members_with_media':0,'not_provided':0,'retry':0}
  for t in tenants:
   cursor=''
   while True:
    batch=await p.wecom(t,'externalcontact/groupchat/list','POST',body={'limit':100,'cursor':cursor})
    for g in batch.get('group_chat_list',[]):
     detail,complete=await w.sync_group(t,g['chat_id'])
     stats['synced_groups']+=1
     for m in detail.get('member_list',[]):
      stats['external_memberships']+=str(m.get('type'))=='2'
      stats['internal_memberships']+=str(m.get('type'))=='1'
      stats['members_with_media']+=bool(m.get('media_id'))
      stats['not_provided']+=m.get('avatar_state')=='not_provided'
      stats['retry']+=m.get('avatar_state')=='retry'
    cursor=batch.get('next_cursor','')
    if not cursor:break
  async with sessions() as db:
   rows=(await db.scalars(select(BusinessEvent))).all()
   after={r.id:(r.event_type,r.quantity,r.resolution,r.occurred_at,r.customer_id,r.employee_id,r.group_id,r.operator_id,r.deletion_direction,r.add_way,r.snapshot) for r in rows}
   stats['business_records_unchanged']=before==after
   stats['event_count']=len(rows)
   stats['media_files_present']=sum((Path(s.media_dir)/m.relative_path).is_file() for m in (await db.scalars(select(Media))).all())
  stats['deployed_prefetch']= 'async def sync_group' in Path('app/worker.py').read_text()
  print(json.dumps(stats))
 finally:await p.close();await engine.dispose()
asyncio.run(main())
